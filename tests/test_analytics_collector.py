from __future__ import annotations

from unittest.mock import MagicMock

from ghostcash.analytics.collector import (
    collect_analytics,
    evaluate_winners,
    select_trackable_published_posts,
)
from ghostcash.config.schema import Config
from ghostcash.publishing.buffer import BufferPublisher


def _insert_content(conn, product_id=None, status="SCHEDULED", **overrides) -> int:
    defaults = dict(topic="t", category="AI", title="title", status=status, product_id=product_id)
    defaults.update(overrides)
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, status, product_id) VALUES (?, ?, ?, ?, ?)",
        (
            defaults["topic"],
            defaults["category"],
            defaults["title"],
            defaults["status"],
            defaults["product_id"],
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_platform(conn, name="YOUTUBE") -> int:
    cursor = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES (?, ?)", (name, "ch1")
    )
    conn.commit()
    return cursor.lastrowid


def _insert_published_post(
    conn, content_id, platform_id, status="SCHEDULED", external_post_id="post1", key="k1"
) -> int:
    cursor = conn.execute(
        "INSERT INTO published_posts "
        "(content_id, platform_id, idempotency_key, external_post_id, status) "
        "VALUES (?, ?, ?, ?, ?)",
        (content_id, platform_id, key, external_post_id, status),
    )
    conn.commit()
    return cursor.lastrowid


def _fake_publisher(post_payload) -> MagicMock:
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_post_status_and_metrics.return_value = post_payload
    return publisher


# -- select_trackable_published_posts --------------------------------------


def test_select_trackable_excludes_pending_and_failed(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    _insert_published_post(
        migrated_conn, content_id, platform_id, status="PENDING", external_post_id=None, key="k1"
    )
    _insert_published_post(migrated_conn, content_id, platform_id, status="FAILED", key="k2")
    _insert_published_post(migrated_conn, content_id, platform_id, status="SCHEDULED", key="k3")

    trackable = select_trackable_published_posts(migrated_conn)

    assert len(trackable) == 1
    assert trackable[0]["status"] == "SCHEDULED"


# -- collect_analytics -------------------------------------------------------


def test_collect_analytics_confirms_sent_post_as_published(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    _insert_published_post(migrated_conn, content_id, platform_id, status="SCHEDULED")

    publisher = _fake_publisher(
        {"id": "post1", "status": "sent", "sentAt": "2026-10-01T00:00:00Z", "metrics": []}
    )

    result = collect_analytics(publisher, migrated_conn)

    assert result.posts_checked == 1
    assert result.posts_marked_sent == 1
    row = migrated_conn.execute("SELECT status, published_at FROM published_posts").fetchone()
    assert row["status"] == "PUBLISHED"
    assert row["published_at"] == "2026-10-01T00:00:00Z"


def test_collect_analytics_upserts_content_performance_from_metrics(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    _insert_published_post(migrated_conn, content_id, platform_id, status="PUBLISHED")

    publisher = _fake_publisher(
        {
            "id": "post1",
            "status": "sent",
            "metrics": [
                {"type": "views", "name": "Views", "value": 1000.0, "unit": "count"},
                {"type": "reactions", "name": "Reactions", "value": 100.0, "unit": "count"},
            ],
        }
    )

    result = collect_analytics(publisher, migrated_conn)

    assert result.metrics_collected == 1
    row = migrated_conn.execute(
        "SELECT views, likes, clicks, engagement_rate FROM content_performance"
    ).fetchone()
    assert row["views"] == 1000
    assert row["likes"] == 100
    assert row["clicks"] is None  # Buffer exposes no click metric
    assert row["engagement_rate"] == 10.0


def test_collect_analytics_is_idempotent_on_rerun(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    _insert_published_post(migrated_conn, content_id, platform_id, status="PUBLISHED")

    publisher = _fake_publisher(
        {
            "id": "post1",
            "status": "sent",
            "metrics": [{"type": "views", "name": "Views", "value": 500.0, "unit": "count"}],
        }
    )

    collect_analytics(publisher, migrated_conn)
    collect_analytics(publisher, migrated_conn)

    rows = migrated_conn.execute("SELECT COUNT(*) AS n FROM content_performance").fetchone()
    assert rows["n"] == 1  # second run updates the same row, doesn't duplicate it


def test_collect_analytics_skips_posts_with_no_metrics_yet(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    _insert_published_post(migrated_conn, content_id, platform_id, status="PUBLISHED")

    publisher = _fake_publisher({"id": "post1", "status": "sent", "metrics": []})

    result = collect_analytics(publisher, migrated_conn)

    assert result.metrics_collected == 0
    rows = migrated_conn.execute("SELECT COUNT(*) AS n FROM content_performance").fetchone()
    assert rows["n"] == 0


# -- evaluate_winners ---------------------------------------------------------


def _config() -> Config:
    return Config()


def test_evaluate_winners_writes_a_snapshot_for_each_content_item(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes) "
        "VALUES (?, ?, 1000, 100)",
        (content_id, platform_id),
    )
    migrated_conn.commit()

    results = evaluate_winners(migrated_conn, _config())

    assert content_id in results
    snapshot = migrated_conn.execute(
        "SELECT entity_type, entity_id, views FROM analytics_snapshots"
    ).fetchone()
    assert snapshot["entity_type"] == "CONTENT"
    assert snapshot["entity_id"] == content_id
    assert snapshot["views"] == 1000


def test_evaluate_winners_excludes_revenue_without_product(migrated_conn):
    content_id = _insert_content(migrated_conn, product_id=None)
    platform_id = _insert_platform(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes, revenue) "
        "VALUES (?, ?, 1000, 100, 0)",
        (content_id, platform_id),
    )
    migrated_conn.commit()

    results = evaluate_winners(migrated_conn, _config())

    assert "revenue" not in results[content_id].components_used
    assert "revenue" in results[content_id].components_missing


def test_evaluate_winners_includes_revenue_with_product(migrated_conn):
    content_id = _insert_content(migrated_conn, product_id=None)
    # products table exists from Phase 1's schema even though product
    # business logic itself is Phase 8 - insert a minimal row so the FK
    # (content.product_id -> products.id) and the has_product check both
    # have something real to point at.
    product_cursor = migrated_conn.execute(
        "INSERT INTO products (name, price) VALUES ('Test product', 9.99)"
    )
    migrated_conn.commit()
    migrated_conn.execute(
        "UPDATE content SET product_id = ? WHERE id = ?", (product_cursor.lastrowid, content_id)
    )
    platform_id = _insert_platform(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes, revenue) "
        "VALUES (?, ?, 1000, 100, 10.0)",
        (content_id, platform_id),
    )
    migrated_conn.commit()

    results = evaluate_winners(migrated_conn, _config())

    assert "revenue" in results[content_id].components_used


def test_evaluate_winners_promotes_content_to_winner_above_threshold(migrated_conn):
    content_id = _insert_content(migrated_conn, status="PUBLISHED")
    platform_id = _insert_platform(migrated_conn)
    # engagement_rate = (likes+comments+shares)/views*100 = 5000/1000 = way
    # above the 10% target -> engagement component clamps to 1.0, which
    # alone clears the default 0.5 threshold once redistributed.
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes) "
        "VALUES (?, ?, 1000, 5000)",
        (content_id, platform_id),
    )
    migrated_conn.commit()

    evaluate_winners(migrated_conn, _config())

    status = migrated_conn.execute(
        "SELECT status FROM content WHERE id = ?", (content_id,)
    ).fetchone()
    assert status["status"] == "WINNER"


def test_evaluate_winners_does_not_promote_failed_content(migrated_conn):
    content_id = _insert_content(migrated_conn, status="FAILED")
    platform_id = _insert_platform(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes) "
        "VALUES (?, ?, 1000, 5000)",
        (content_id, platform_id),
    )
    migrated_conn.commit()

    evaluate_winners(migrated_conn, _config())

    status = migrated_conn.execute(
        "SELECT status FROM content WHERE id = ?", (content_id,)
    ).fetchone()
    assert status["status"] == "FAILED"


def test_evaluate_winners_aggregates_across_platforms(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_a = _insert_platform(migrated_conn, name="YOUTUBE")
    platform_b = _insert_platform(migrated_conn, name="INSTAGRAM")
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes) "
        "VALUES (?, ?, 1000, 100)",
        (content_id, platform_a),
    )
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes) "
        "VALUES (?, ?, 2000, 200)",
        (content_id, platform_b),
    )
    migrated_conn.commit()

    evaluate_winners(migrated_conn, _config())

    snapshot = migrated_conn.execute(
        "SELECT views, likes FROM analytics_snapshots WHERE entity_id = ?", (content_id,)
    ).fetchone()
    assert snapshot["views"] == 3000
    assert snapshot["likes"] == 300


def test_evaluate_winners_writes_per_platform_winner_score(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes) "
        "VALUES (?, ?, 1000, 100)",
        (content_id, platform_id),
    )
    migrated_conn.commit()

    evaluate_winners(migrated_conn, _config())

    row = migrated_conn.execute("SELECT winner_score FROM content_performance").fetchone()
    assert row["winner_score"] is not None
