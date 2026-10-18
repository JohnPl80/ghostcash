from __future__ import annotations

from datetime import UTC, datetime

from ghostcash.config.schema import Config
from ghostcash.dashboard.queries import (
    get_content_counts,
    get_content_list,
    get_failed_jobs,
    get_opportunities,
    get_order_counts,
    get_pending_approval_count,
    get_recent_jobs,
    get_revenue_summary,
    get_system_status,
    get_top_categories,
    get_top_content,
    get_top_products,
)


def _insert_order(conn, **overrides):
    defaults = dict(
        external_sale_id="sale",
        product_id=None,
        amount=10.0,
        status="COMPLETED",
        ordered_at=None,
    )
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO orders (product_id, external_sale_id, amount, status, ordered_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            defaults["product_id"],
            defaults["external_sale_id"],
            defaults["amount"],
            defaults["status"],
            defaults["ordered_at"],
        ),
    )
    conn.commit()


# -- revenue_summary --------------------------------------------------------------


def test_get_revenue_summary_buckets_by_today_week_and_month(migrated_conn):
    # Thursday the 8th: month_start = 1st, week_start = Monday the 5th.
    now = datetime(2026, 10, 8, 15, 0, 0, tzinfo=UTC)

    _insert_order(
        migrated_conn, external_sale_id="today", amount=10.0, ordered_at="2026-10-08T10:00:00.000Z"
    )
    _insert_order(
        migrated_conn, external_sale_id="week", amount=5.0, ordered_at="2026-10-06T09:00:00.000Z"
    )
    _insert_order(
        migrated_conn, external_sale_id="month", amount=7.0, ordered_at="2026-10-03T09:00:00.000Z"
    )
    _insert_order(
        migrated_conn,
        external_sale_id="last_month",
        amount=999.0,
        ordered_at="2026-09-20T09:00:00.000Z",
    )
    _insert_order(
        migrated_conn,
        external_sale_id="refunded_today",
        amount=100.0,
        status="REFUNDED",
        ordered_at="2026-10-08T11:00:00.000Z",
    )

    summary = get_revenue_summary(migrated_conn, now=now)

    assert summary.today == 10.0
    assert summary.this_week == 15.0
    assert summary.this_month == 22.0


def test_get_revenue_summary_falls_back_to_created_at_when_ordered_at_is_null(migrated_conn):
    now = datetime.now(UTC)
    _insert_order(migrated_conn, external_sale_id="no_ordered_at", amount=42.0, ordered_at=None)

    summary = get_revenue_summary(migrated_conn, now=now)

    assert summary.today == 42.0


# -- order / content counts -------------------------------------------------------


def test_get_order_counts(migrated_conn):
    _insert_order(migrated_conn, external_sale_id="a", status="COMPLETED")
    _insert_order(migrated_conn, external_sale_id="b", status="COMPLETED")
    _insert_order(migrated_conn, external_sale_id="c", status="REFUNDED")
    _insert_order(migrated_conn, external_sale_id="d", status="CHARGED_BACK")

    counts = get_order_counts(migrated_conn)

    assert counts.total == 4
    assert counts.completed == 2
    assert counts.refunded_or_chargedback == 2


def test_get_content_counts_published_comes_from_published_posts_not_content_status(
    migrated_conn,
):
    # content.status only ever reaches SCHEDULED/WINNER in this codebase -
    # "published" must be read from published_posts, not content.status.
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('a', 'AI', 'SCHEDULED')"
    )
    scheduled_id = migrated_conn.execute("SELECT id FROM content").fetchone()["id"]
    platform_id = migrated_conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
    ).lastrowid
    migrated_conn.execute(
        "INSERT INTO published_posts (content_id, platform_id, idempotency_key, status) "
        "VALUES (?, ?, 'k1', 'PUBLISHED')",
        (scheduled_id, platform_id),
    )
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('b', 'AI', 'READY')"
    )
    migrated_conn.commit()

    counts = get_content_counts(migrated_conn)

    assert counts.published == 1
    # "queued" mirrors publishing/queue.py's own definition: READY content
    # plus content with a post still waiting in Buffer. The SCHEDULED row
    # here has only a PUBLISHED post, so only the READY row is queued.
    assert counts.queued == 1


def test_get_content_list_filters_by_status(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('a', 'AI', 'READY')"
    )
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('b', 'AI', 'REJECTED')"
    )
    migrated_conn.commit()

    all_rows = get_content_list(migrated_conn)
    ready_rows = get_content_list(migrated_conn, status="READY")

    assert len(all_rows) == 2
    assert len(ready_rows) == 1
    assert ready_rows[0]["topic"] == "a"


# -- top content / categories / products ------------------------------------------


def test_get_top_content_ranks_by_best_platform_winner_score(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('a', 'AI', 'WINNER')"
    )
    content_id = migrated_conn.execute("SELECT id FROM content").fetchone()["id"]
    yt = migrated_conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
    ).lastrowid
    ig = migrated_conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('INSTAGRAM', 'ch2')"
    ).lastrowid
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, winner_score) "
        "VALUES (?, ?, 1000, 0.3)",
        (content_id, yt),
    )
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, winner_score) "
        "VALUES (?, ?, 2000, 0.9)",
        (content_id, ig),
    )
    migrated_conn.commit()

    top = get_top_content(migrated_conn)

    assert len(top) == 1
    assert top[0]["winner_score"] == 0.9  # best platform, not average
    assert top[0]["views"] == 3000  # summed across platforms


def test_get_top_categories_ranks_by_revenue_then_falls_back_to_views(migrated_conn):
    for name, category, views, revenue in [
        ("a", "EXCEL", 100, 0.0),
        ("b", "AI", 50, 10.0),
    ]:
        migrated_conn.execute(
            "INSERT INTO content (topic, category, status) VALUES (?, ?, 'PUBLISHED')",
            (name, category),
        )
        content_id = migrated_conn.execute(
            "SELECT id FROM content WHERE topic = ?", (name,)
        ).fetchone()["id"]
        platform_id = migrated_conn.execute(
            "SELECT id FROM platforms WHERE name = 'YOUTUBE'"
        ).fetchone()
        if platform_id is None:
            platform_id = migrated_conn.execute(
                "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
            ).lastrowid
        else:
            platform_id = platform_id["id"]
        migrated_conn.execute(
            "INSERT INTO content_performance (content_id, platform_id, views, revenue) "
            "VALUES (?, ?, ?, ?)",
            (content_id, platform_id, views, revenue),
        )
    migrated_conn.commit()

    top = get_top_categories(migrated_conn)

    # AI has real revenue (10.0) so it ranks first even though EXCEL has
    # more views - revenue beats views whenever any revenue exists.
    assert top[0]["category"] == "AI"
    assert top[0]["total_revenue"] == 10.0
    assert top[1]["category"] == "EXCEL"


def test_get_top_products_counts_only_completed_revenue(migrated_conn):
    migrated_conn.execute("INSERT INTO products (name, status) VALUES ('P', 'ACTIVE')")
    product_id = migrated_conn.execute("SELECT id FROM products").fetchone()["id"]
    _insert_order(
        migrated_conn, external_sale_id="s1", product_id=product_id, amount=20.0, status="COMPLETED"
    )
    _insert_order(
        migrated_conn, external_sale_id="s2", product_id=product_id, amount=30.0, status="REFUNDED"
    )
    migrated_conn.commit()

    top = get_top_products(migrated_conn)

    assert len(top) == 1
    assert top[0]["total_revenue"] == 20.0
    assert top[0]["completed_orders"] == 1


# -- jobs / opportunities ---------------------------------------------------------


def test_get_failed_jobs_and_recent_jobs(migrated_conn):
    migrated_conn.execute("INSERT INTO jobs (job_type, status) VALUES ('generate', 'SUCCEEDED')")
    migrated_conn.execute(
        "INSERT INTO jobs (job_type, status, error) VALUES ('render', 'FAILED', 'boom')"
    )
    migrated_conn.commit()

    failed = get_failed_jobs(migrated_conn)
    recent = get_recent_jobs(migrated_conn)

    assert len(failed) == 1
    assert failed[0]["job_type"] == "render"
    assert len(recent) == 2


def test_get_pending_approval_count_and_get_opportunities(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO product_opportunities (proposed_name, status) VALUES ('X', 'PENDING_APPROVAL')"
    )
    migrated_conn.execute(
        "INSERT INTO product_opportunities (proposed_name, status) VALUES ('Y', 'APPROVED')"
    )
    migrated_conn.commit()

    assert get_pending_approval_count(migrated_conn) == 1
    assert len(get_opportunities(migrated_conn)) == 2
    assert len(get_opportunities(migrated_conn, status="APPROVED")) == 1


# -- system status (owner view, section 41) --------------------------------------


def test_get_system_status_flags_pending_approval_and_failed_jobs(migrated_conn, tmp_path):
    migrated_conn.execute(
        "INSERT INTO product_opportunities (proposed_name, status) VALUES ('X', 'PENDING_APPROVAL')"
    )
    migrated_conn.execute(
        "INSERT INTO jobs (job_type, status, error) VALUES ('render', 'FAILED', 'boom')"
    )
    migrated_conn.commit()

    status = get_system_status(migrated_conn, Config(), str(tmp_path / "irrelevant.db"))

    assert not status.ok
    assert any("awaiting approval" in item for item in status.attention_items)
    assert any("failed job" in item for item in status.attention_items)


def test_get_system_status_flags_low_queue(migrated_conn, tmp_path):
    # No READY/SCHEDULED content at all -> 0 days remaining, always below
    # both thresholds for a brand new, empty database.
    status = get_system_status(migrated_conn, Config(), str(tmp_path / "irrelevant.db"))

    assert not status.ok
    assert any("queue" in item.lower() for item in status.attention_items)


def test_get_system_status_ok_when_nothing_outstanding(migrated_conn, tmp_path):
    config = Config()
    posts_per_day = config.publishing.posts_per_day
    for i in range(config.queue.emergency_threshold_days * posts_per_day):
        migrated_conn.execute(
            "INSERT INTO content (topic, category, status) VALUES (?, 'AI', 'READY')", (f"t{i}",)
        )
    migrated_conn.commit()

    # check_system() will still report its own issues here (no
    # content_categories.yaml / templates in this bare tmp_path cwd) -
    # this test only asserts the *queue* signal clears, not full health.
    status = get_system_status(migrated_conn, config, str(tmp_path / "irrelevant.db"))

    assert not any("queue" in item.lower() for item in status.attention_items)
