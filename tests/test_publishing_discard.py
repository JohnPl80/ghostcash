"""Clearing unwanted drafts out of Buffer (ghostcash drafts discard)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import ghostcash.cli.commands.drafts as drafts_cmd
from ghostcash.cli.main import main
from ghostcash.config.schema import Config
from ghostcash.publishing.base import PostNotFoundError, PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.discard import (
    discard_drafts,
    select_discardable_drafts,
)
from ghostcash.publishing.queue import get_queue_status

# -- helpers ---------------------------------------------------------------


def _platform(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM platforms WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    cursor = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES (?, ?)", (name, f"ch-{name}")
    )
    conn.commit()
    return cursor.lastrowid


def _content(conn, title="video", status="SCHEDULED", created_at="2026-10-18T02:00:00.000Z") -> int:
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, status, created_at) "
        "VALUES ('t', 'AI', ?, ?, ?)",
        (title, status, created_at),
    )
    conn.commit()
    return cursor.lastrowid


_post_counter = 0


def _post(conn, content_id, platform="YOUTUBE", status="DRAFT", external="auto") -> int:
    global _post_counter
    _post_counter += 1
    if external == "auto":
        external = f"buf-{_post_counter}"
    cursor = conn.execute(
        "INSERT INTO published_posts "
        "(content_id, platform_id, idempotency_key, external_post_id, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (content_id, _platform(conn, platform), f"key-{_post_counter}", external, status),
    )
    conn.commit()
    return cursor.lastrowid


def _draft_video(conn, title="video", created_at="2026-10-18T02:00:00.000Z") -> int:
    """A video waiting as two drafts (YouTube + Instagram), like the real pile."""
    content_id = _content(conn, title=title, created_at=created_at)
    _post(conn, content_id, "YOUTUBE")
    _post(conn, content_id, "INSTAGRAM")
    return content_id


def _publisher() -> MagicMock:
    return MagicMock(spec=BufferPublisher)


def _status(conn, content_id) -> str:
    return conn.execute("SELECT status FROM content WHERE id = ?", (content_id,)).fetchone()[0]


# -- select_discardable_drafts ---------------------------------------------


def test_selects_draft_videos_oldest_first_with_their_platforms(migrated_conn):
    first = _draft_video(migrated_conn, "first")
    second = _draft_video(migrated_conn, "second")

    candidates = select_discardable_drafts(migrated_conn)

    assert [c.content_id for c in candidates] == [first, second]
    assert candidates[0].title == "first"
    assert [p.platform for p in candidates[0].posts] == ["YOUTUBE", "INSTAGRAM"]
    assert all(p.external_post_id for p in candidates[0].posts)


def test_never_selects_a_video_that_is_scheduled_or_already_published(migrated_conn):
    scheduled = _content(migrated_conn)
    _post(migrated_conn, scheduled, "YOUTUBE", status="DRAFT")
    _post(migrated_conn, scheduled, "INSTAGRAM", status="SCHEDULED")
    published = _content(migrated_conn)
    _post(migrated_conn, published, "YOUTUBE", status="DRAFT")
    _post(migrated_conn, published, "INSTAGRAM", status="PUBLISHED")

    assert select_discardable_drafts(migrated_conn) == []


def test_ignores_content_that_is_not_waiting_in_buffer(migrated_conn):
    for status in ("READY", "REJECTED", "PUBLISHED", "WINNER", "APPROVED"):
        content_id = _content(migrated_conn, status=status)
        _post(migrated_conn, content_id)
    no_external_id = _content(migrated_conn)
    _post(migrated_conn, no_external_id, external=None)

    assert select_discardable_drafts(migrated_conn) == []


def test_limit_and_created_before_narrow_the_selection(migrated_conn):
    old_a = _draft_video(migrated_conn, "a", created_at="2026-10-18T02:00:00.000Z")
    old_b = _draft_video(migrated_conn, "b", created_at="2026-10-21T23:59:00.000Z")
    new = _draft_video(migrated_conn, "c", created_at="2026-10-22T00:01:00.000Z")

    assert [c.content_id for c in select_discardable_drafts(migrated_conn, limit=1)] == [old_a]
    before = select_discardable_drafts(migrated_conn, created_before="2026-10-22")
    assert [c.content_id for c in before] == [old_a, old_b]
    assert new not in [c.content_id for c in before]


# -- discard_drafts --------------------------------------------------------


def test_deletes_every_draft_and_rejects_the_content(migrated_conn):
    video = _draft_video(migrated_conn)
    publisher = _publisher()
    sleep = MagicMock()

    result = discard_drafts(
        migrated_conn, publisher, select_discardable_drafts(migrated_conn), sleep=sleep
    )

    assert result.discarded == [video]
    assert result.posts_deleted == 2
    assert publisher.delete_post.call_count == 2
    assert _status(migrated_conn, video) == "REJECTED"
    assert sleep.call_count == 1  # a pause between the two requests, none before the first


def test_removed_videos_stop_counting_towards_the_queue(migrated_conn):
    for index in range(3):
        _draft_video(migrated_conn, f"v{index}")
    config = Config()
    before = get_queue_status(migrated_conn, config)
    assert before.scheduled_count == 3

    discard_drafts(
        migrated_conn, _publisher(), select_discardable_drafts(migrated_conn), sleep=MagicMock()
    )

    assert get_queue_status(migrated_conn, config).scheduled_count == 0


def test_a_draft_that_is_already_gone_counts_as_removed(migrated_conn):
    video = _draft_video(migrated_conn)
    publisher = _publisher()
    publisher.delete_post.side_effect = [PostNotFoundError("gone"), True]

    result = discard_drafts(
        migrated_conn, publisher, select_discardable_drafts(migrated_conn), sleep=MagicMock()
    )

    assert result.discarded == [video]
    assert result.posts_deleted == 1
    assert result.posts_already_gone == 1
    assert _status(migrated_conn, video) == "REJECTED"


def test_a_failed_deletion_leaves_the_video_alone_and_a_rerun_finishes_it(migrated_conn):
    video = _draft_video(migrated_conn)
    publisher = _publisher()
    # YouTube draft deleted, Instagram draft fails.
    publisher.delete_post.side_effect = [True, PublisherError("Buffer is down")]

    first = discard_drafts(
        migrated_conn, publisher, select_discardable_drafts(migrated_conn), sleep=MagicMock()
    )

    assert first.discarded == []
    assert first.failures and first.failures[0][0] == video
    assert "INSTAGRAM" in first.failures[0][1]
    assert _status(migrated_conn, video) == "SCHEDULED"

    # Second run: the YouTube draft is already gone, the Instagram one now works.
    publisher.delete_post.side_effect = [PostNotFoundError("gone"), True]
    second = discard_drafts(
        migrated_conn, publisher, select_discardable_drafts(migrated_conn), sleep=MagicMock()
    )

    assert second.discarded == [video]
    assert second.posts_already_gone == 1
    assert _status(migrated_conn, video) == "REJECTED"


def test_one_failing_video_does_not_stop_the_others(migrated_conn):
    bad = _draft_video(migrated_conn, "bad")
    good = _draft_video(migrated_conn, "good")
    publisher = _publisher()
    publisher.delete_post.side_effect = [PublisherError("nope"), True, True]

    result = discard_drafts(
        migrated_conn, publisher, select_discardable_drafts(migrated_conn), sleep=MagicMock()
    )

    assert result.discarded == [good]
    assert [content_id for content_id, _ in result.failures] == [bad]
    assert not result.stopped_early


def test_three_failures_in_a_row_stop_the_run(migrated_conn):
    for index in range(6):
        _draft_video(migrated_conn, f"v{index}")
    publisher = _publisher()
    publisher.delete_post.side_effect = PublisherError("Not authorized")

    result = discard_drafts(
        migrated_conn, publisher, select_discardable_drafts(migrated_conn), sleep=MagicMock()
    )

    assert result.stopped_early
    assert result.discarded == []
    assert len(result.failures) == 3
    assert publisher.delete_post.call_count == 3  # it did not keep hammering Buffer


# -- the command -----------------------------------------------------------


@pytest.fixture()
def project(isolated_project: Path, monkeypatch):
    """An initialised project with three draft videos in its database."""
    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")
    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    conn.row_factory = sqlite3.Row
    for index in range(3):
        _draft_video(conn, f"video {index}", created_at=f"2026-10-1{index + 5}T02:00:00.000Z")
    conn.close()
    return isolated_project


def _db(project: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(project / "data" / "ghostcash.db")
    conn.row_factory = sqlite3.Row
    return conn


def _fake_buffer(monkeypatch) -> MagicMock:
    publisher = MagicMock(spec=BufferPublisher)
    monkeypatch.setattr(drafts_cmd, "get_publisher", lambda config: publisher)
    return publisher


def test_without_apply_it_only_previews_and_never_contacts_buffer(project, monkeypatch, capsys):
    def must_not_be_called(config):
        raise AssertionError("a preview must not contact Buffer")

    monkeypatch.setattr(drafts_cmd, "get_publisher", must_not_be_called)

    assert main(["drafts", "discard", "--all"]) == 0

    out = capsys.readouterr().out
    assert "3 video(s) are waiting as drafts" in out
    assert "Preview only" in out
    assert "--apply" in out
    conn = _db(project)
    assert conn.execute("SELECT COUNT(*) FROM content WHERE status = 'REJECTED'").fetchone()[0] == 0


def test_the_default_covers_a_single_video(project, monkeypatch, capsys):
    publisher = _fake_buffer(monkeypatch)

    assert main(["drafts", "discard", "--apply"]) == 0

    out = capsys.readouterr().out
    assert "This run covers 1 of them" in out
    assert "2 more video(s) are still waiting as drafts" in out
    assert publisher.delete_post.call_count == 2
    conn = _db(project)
    rejected = [r[0] for r in conn.execute("SELECT id FROM content WHERE status = 'REJECTED'")]
    assert rejected == [1]  # the oldest


def test_all_apply_removes_every_draft_video_and_records_a_job(project, monkeypatch, capsys):
    publisher = _fake_buffer(monkeypatch)

    assert main(["drafts", "discard", "--all", "--apply"]) == 0

    assert publisher.delete_post.call_count == 6
    assert "No more videos are waiting as drafts" in capsys.readouterr().out
    conn = _db(project)
    assert conn.execute("SELECT COUNT(*) FROM content WHERE status = 'REJECTED'").fetchone()[0] == 3
    job = conn.execute("SELECT status FROM jobs WHERE job_type = 'discard_drafts'").fetchone()
    assert job[0] == "SUCCEEDED"


def test_created_before_keeps_newer_videos(project, monkeypatch, capsys):
    publisher = _fake_buffer(monkeypatch)

    # The three videos were made on 15, 16 and 17 October.
    assert main(["drafts", "discard", "--all", "--created-before", "2026-10-17", "--apply"]) == 0

    assert publisher.delete_post.call_count == 4
    conn = _db(project)
    statuses = [r[0] for r in conn.execute("SELECT status FROM content ORDER BY id")]
    assert statuses == ["REJECTED", "REJECTED", "SCHEDULED"]


def test_apply_without_a_buffer_key_deletes_nothing(project, monkeypatch, capsys):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    publisher = _fake_buffer(monkeypatch)

    assert main(["drafts", "discard", "--apply"]) == 1

    assert "BUFFER_API_KEY" in capsys.readouterr().out
    publisher.delete_post.assert_not_called()


def test_a_failure_gives_exit_code_one_and_names_the_video(project, monkeypatch, capsys):
    publisher = _fake_buffer(monkeypatch)
    publisher.delete_post.side_effect = PublisherError("Buffer is down")

    assert main(["drafts", "discard", "--limit", "2", "--apply"]) == 1

    out = capsys.readouterr().out
    assert "[FAILED]" in out
    assert "Buffer is down" in out
    conn = _db(project)
    assert conn.execute("SELECT COUNT(*) FROM content WHERE status = 'REJECTED'").fetchone()[0] == 0


def test_a_bad_date_or_limit_is_refused_cleanly(project, capsys):
    assert main(["drafts", "discard", "--created-before", "yesterday"]) == 1
    assert "--created-before" in capsys.readouterr().out
    assert main(["drafts", "discard", "--limit", "0"]) == 1
    assert "--limit" in capsys.readouterr().out


def test_limit_and_all_cannot_be_combined(project):
    with pytest.raises(SystemExit) as excinfo:
        main(["drafts", "discard", "--limit", "5", "--all"])
    assert excinfo.value.code == 2


def test_nothing_to_discard_is_not_an_error(isolated_project: Path, capsys):
    main(["init"])
    assert main(["drafts", "discard", "--all", "--apply"]) == 0
    assert "nothing to discard" in capsys.readouterr().out


def test_it_fails_cleanly_without_init(isolated_project: Path, capsys):
    assert main(["drafts", "discard"]) == 1
    assert "[error]" in capsys.readouterr().out
