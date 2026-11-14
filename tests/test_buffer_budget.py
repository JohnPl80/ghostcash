"""Staying inside Buffer's API request allowance.

Buffer's free plan allows 1,000 requests per channel per 30 days (rolling).
On 2026-11-13 GhostCash used Shortcut Club's up (hourly health pings, a full
account/channel/queue lookup in every 4-hourly run, and every post checked
every day), and because the health check treated the refusal as a failure,
every run then stopped before writing or rendering anything.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from ghostcash.analytics.collector import collect_analytics, select_posts_due
from ghostcash.config.schema import Config
from ghostcash.health import check_system
from ghostcash.publishing.base import PublisherError, RateLimitError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.channels import (
    resolve_organization_id,
    sync_channels_if_stale,
)

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)
LIMIT_BODY = {
    "errors": [
        {
            "message": "Too many requests from this client. Please try again later.",
            "extensions": {"code": "RATE_LIMIT_EXCEEDED", "window": "30d"},
        }
    ]
}


def _response(status_code: int, body: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = body
    response.text = str(body)
    return response


# ---------------------------------------------------------------------
# Recognising the limit
# ---------------------------------------------------------------------


@pytest.mark.parametrize("status_code", [429, 200])
def test_rate_limit_is_its_own_error(status_code):
    session = MagicMock()
    session.post.return_value = _response(status_code, LIMIT_BODY)
    publisher = BufferPublisher(api_key="k", session=session)
    with pytest.raises(RateLimitError):
        publisher.get_account()


def test_other_buffer_errors_are_not_rate_limits():
    session = MagicMock()
    session.post.return_value = _response(200, {"errors": [{"message": "Unauthorized"}]})
    publisher = BufferPublisher(api_key="k", session=session)
    with pytest.raises(PublisherError) as caught:
        publisher.get_account()
    assert not isinstance(caught.value, RateLimitError)


# ---------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------


def _health_config(tmp_path) -> Config:
    config = Config()
    config.buffer_api_key = "k"
    config.database_path = str(tmp_path / "g.db")
    return config


def test_health_skip_buffer_makes_no_request(tmp_path, monkeypatch):
    import ghostcash.health as health

    called = MagicMock(side_effect=AssertionError("Buffer must not be contacted"))
    monkeypatch.setattr(health, "get_publisher", called)
    result = check_system(_health_config(tmp_path), None, check_buffer=False)
    assert any("Buffer API not checked" in note for note in result.notes)
    assert not any("Buffer" in issue for issue in result.issues)


def test_health_rate_limit_is_a_warning_not_an_issue(tmp_path, monkeypatch):
    import ghostcash.health as health

    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_account.side_effect = RateLimitError("Buffer API HTTP error 429: too many")
    monkeypatch.setattr(health, "get_publisher", lambda config: publisher)

    result = check_system(_health_config(tmp_path), None)

    assert not any("Buffer" in issue for issue in result.issues)
    assert any("allowance is used up" in note for note in result.notes)


def test_health_other_buffer_errors_still_fail(tmp_path, monkeypatch):
    import ghostcash.health as health

    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_account.side_effect = PublisherError("Buffer API error: Unauthorized")
    monkeypatch.setattr(health, "get_publisher", lambda config: publisher)

    result = check_system(_health_config(tmp_path), None)
    assert any("Buffer API not reachable" in issue for issue in result.issues)


# ---------------------------------------------------------------------
# Organization and channel lookups are remembered
# ---------------------------------------------------------------------


def test_organization_id_is_looked_up_once(migrated_conn):
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_organizations.return_value = [{"id": "org1", "name": "Org"}]
    assert resolve_organization_id(publisher, Config(), migrated_conn) == "org1"
    assert resolve_organization_id(publisher, Config(), migrated_conn) == "org1"
    assert publisher.get_organizations.call_count == 1


def test_channels_are_synced_at_most_every_20_hours(migrated_conn):
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_channels.return_value = [
        {"id": "ch1", "service": "youtube", "displayName": "YT"},
    ]
    assert sync_channels_if_stale(publisher, migrated_conn, "org1", NOW) is not None
    assert (
        sync_channels_if_stale(publisher, migrated_conn, "org1", NOW + timedelta(hours=5)) is None
    )
    assert (
        sync_channels_if_stale(publisher, migrated_conn, "org1", NOW + timedelta(hours=21))
        is not None
    )
    assert publisher.get_channels.call_count == 2


def test_channels_are_synced_when_none_are_known(migrated_conn):
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_channels.return_value = []
    sync_channels_if_stale(publisher, migrated_conn, "org1", NOW)
    sync_channels_if_stale(publisher, migrated_conn, "org1", NOW + timedelta(hours=1))
    assert publisher.get_channels.call_count == 2


# ---------------------------------------------------------------------
# Publishing
# ---------------------------------------------------------------------


def test_publish_rate_limit_holds_without_failing_the_run(isolated_project, monkeypatch, capsys):
    import ghostcash.cli.commands.publish as publish_cmd
    from ghostcash.cli.main import main

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "k")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with sqlite3.connect(isolated_project / "data" / "ghostcash.db") as db:
        db.execute("INSERT INTO content (topic, category, status) VALUES ('t', 'EXCEL', 'READY')")
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_organizations.side_effect = RateLimitError("Buffer API HTTP error 429")
    monkeypatch.setattr(publish_cmd, "get_publisher", lambda config: publisher)
    monkeypatch.setattr(publish_cmd, "get_media_host", lambda config: MagicMock())
    capsys.readouterr()

    assert main(["publish"]) == 0

    out = capsys.readouterr().out
    assert "[hold]" in out
    assert "::warning title=Buffer request limit::" in out
    with sqlite3.connect(isolated_project / "data" / "ghostcash.db") as db:
        assert db.execute("SELECT status FROM content").fetchone()[0] == "READY"


def test_publish_with_nothing_ready_makes_no_request(isolated_project, monkeypatch, capsys):
    import ghostcash.cli.commands.publish as publish_cmd
    from ghostcash.cli.main import main

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "k")
    monkeypatch.setattr(
        publish_cmd, "get_publisher", MagicMock(side_effect=AssertionError("no Buffer"))
    )
    assert main(["publish"]) == 0
    assert "Buffer was not contacted" in capsys.readouterr().out


# ---------------------------------------------------------------------
# Analytics: due posts only, capped, rate limit stops cleanly
# ---------------------------------------------------------------------


def _platform(conn) -> int:
    return conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'yt')"
    ).lastrowid


def _post(
    conn,
    platform_id: int,
    *,
    status: str = "PUBLISHED",
    sent_days_ago: float | None = 1,
    collected_days_ago: float | None = None,
    scheduled_in_hours: float | None = None,
) -> int:
    content_id = conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('t', 'EXCEL', 'SCHEDULED')"
    ).lastrowid
    sent = (NOW - timedelta(days=sent_days_ago)).isoformat() if sent_days_ago is not None else None
    scheduled = (
        (NOW + timedelta(hours=scheduled_in_hours)).isoformat()
        if scheduled_in_hours is not None
        else None
    )
    post_id = conn.execute(
        "INSERT INTO published_posts (content_id, platform_id, idempotency_key, "
        "external_post_id, status, published_at, scheduled_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (content_id, platform_id, f"k{content_id}", f"ext{content_id}", status, sent, scheduled),
    ).lastrowid
    if collected_days_ago is not None:
        conn.execute(
            "INSERT INTO content_performance (content_id, platform_id, views, collected_at) "
            "VALUES (?, ?, 1, ?)",
            (content_id, platform_id, (NOW - timedelta(days=collected_days_ago)).isoformat()),
        )
    conn.commit()
    return post_id


def test_only_due_posts_are_selected(migrated_conn):
    p = _platform(migrated_conn)
    never = _post(migrated_conn, p, sent_days_ago=1)
    young_due = _post(migrated_conn, p, sent_days_ago=2, collected_days_ago=1)
    young_fresh = _post(migrated_conn, p, sent_days_ago=2, collected_days_ago=0.2)
    week_old_due = _post(migrated_conn, p, sent_days_ago=10, collected_days_ago=3)
    week_old_fresh = _post(migrated_conn, p, sent_days_ago=10, collected_days_ago=1)
    month_old_fresh = _post(migrated_conn, p, sent_days_ago=40, collected_days_ago=3)
    queued_future = _post(
        migrated_conn, p, status="SCHEDULED", sent_days_ago=None, scheduled_in_hours=5
    )
    queued_past = _post(
        migrated_conn, p, status="SCHEDULED", sent_days_ago=None, scheduled_in_hours=-5
    )

    rows, skipped = select_posts_due(migrated_conn, 0, NOW)
    chosen = {row["id"] for row in rows}

    assert chosen == {never, young_due, week_old_due, queued_past}
    assert not chosen & {young_fresh, week_old_fresh, month_old_fresh, queued_future}
    assert skipped == 4


def test_cap_keeps_the_most_overdue(migrated_conn):
    p = _platform(migrated_conn)
    _post(migrated_conn, p, sent_days_ago=50, collected_days_ago=8)  # 8/7 overdue
    most = _post(migrated_conn, p, sent_days_ago=2, collected_days_ago=3)  # 3/1 overdue
    never = _post(migrated_conn, p, sent_days_ago=1)

    rows, skipped = select_posts_due(migrated_conn, 2, NOW)

    assert [row["id"] for row in rows] == [never, most]
    assert skipped == 1


def test_collection_stops_cleanly_at_the_rate_limit(migrated_conn):
    p = _platform(migrated_conn)
    _post(migrated_conn, p, sent_days_ago=1)
    _post(migrated_conn, p, sent_days_ago=1)
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_post_status_and_metrics.side_effect = [
        {"status": "sent", "metrics": []},
        RateLimitError("429"),
    ]

    result = collect_analytics(publisher, migrated_conn, 0, NOW)

    assert result.rate_limited is True
    assert result.posts_checked == 1
    assert publisher.get_post_status_and_metrics.call_count == 2


def test_default_analytics_cap():
    assert Config().analytics.max_posts_per_run == 20
