"""Data-gathering for the dashboard (CLAUDE.md sections 39-41) and
`ghostcash report` (section 33 step 13).

Deliberately plain functions returning dataclasses/`sqlite3.Row`
objects - nothing here knows about FastAPI or HTML. `dashboard/app.py`
renders this data as HTML pages, `cli/commands/report.py` renders the
exact same data as plain text for CI logs, so there is one source of
truth for "what does the system's current state look like" rather than
two copies that could drift.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ghostcash.config.schema import Config
from ghostcash.health import check_system
from ghostcash.publishing.queue import get_queue_status


def _iso(dt: datetime) -> str:
    """Format a datetime to match every table's own
    `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` default column format, so a
    boundary comparison against a stored timestamp is a plain, correct
    string comparison - both sides are the same fixed-width ISO-8601
    shape, so lexicographic order matches chronological order."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


# -- Revenue (section 40) -------------------------------------------------------


@dataclass
class RevenueSummary:
    today: float
    this_week: float
    this_month: float


def _revenue_since(conn: sqlite3.Connection, since: datetime) -> float:
    row = conn.execute(
        "SELECT SUM(amount) AS total FROM orders "
        "WHERE status = 'COMPLETED' AND COALESCE(ordered_at, created_at) >= ?",
        (_iso(since),),
    ).fetchone()
    return float(row["total"] or 0.0)


def get_revenue_summary(conn: sqlite3.Connection, *, now: datetime | None = None) -> RevenueSummary:
    now = now or datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())  # Monday
    month_start = today_start.replace(day=1)
    return RevenueSummary(
        today=_revenue_since(conn, today_start),
        this_week=_revenue_since(conn, week_start),
        this_month=_revenue_since(conn, month_start),
    )


# -- Orders / content counts (section 40) ----------------------------------------


@dataclass
class OrderCounts:
    total: int
    completed: int
    refunded_or_chargedback: int


def get_order_counts(conn: sqlite3.Connection) -> OrderCounts:
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed, "
        "SUM(CASE WHEN status IN ('REFUNDED', 'CHARGED_BACK') THEN 1 ELSE 0 END) AS bad "
        "FROM orders"
    ).fetchone()
    return OrderCounts(
        total=row["total"] or 0,
        completed=row["completed"] or 0,
        refunded_or_chargedback=row["bad"] or 0,
    )


@dataclass
class ContentCounts:
    published: int
    queued: int


def get_content_counts(conn: sqlite3.Connection) -> ContentCounts:
    # "Published" is read from `published_posts`, not `content.status` -
    # content.status currently only reaches SCHEDULED or WINNER (see
    # publishing/pipeline.py and analytics/collector.py); whether a post
    # actually went out is recorded per-platform in published_posts.
    published = conn.execute(
        "SELECT COUNT(DISTINCT content_id) FROM published_posts WHERE status = 'PUBLISHED'"
    ).fetchone()[0]
    queued = conn.execute(
        "SELECT COUNT(*) FROM content WHERE status IN ('READY', 'SCHEDULED')"
    ).fetchone()[0]
    return ContentCounts(published=published, queued=queued)


# -- Top content / categories / products (section 40) -----------------------------


def get_top_content(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """Content ranked by Winner Score. `content_performance.winner_score`
    is stored per (content, platform) row, so a content item with
    several platforms is ranked by its *best* platform - a video that's
    a clear winner on YouTube but mediocre on Instagram is still a
    winning piece of content."""
    return conn.execute(
        """
        SELECT c.id, c.topic, c.category, c.status,
               MAX(cp.winner_score) AS winner_score,
               SUM(cp.views) AS views
        FROM content c
        JOIN content_performance cp ON cp.content_id = c.id
        WHERE cp.winner_score IS NOT NULL
        GROUP BY c.id
        ORDER BY winner_score DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_top_categories(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    """Categories ranked by total attributed revenue, falling back to
    total views as a tiebreaker (and as the only signal at all before
    any product exists) - section 31 tracks performance "by category"
    for the optimization loop this feeds."""
    return conn.execute(
        """
        SELECT c.category,
               COUNT(DISTINCT c.id) AS content_count,
               COALESCE(SUM(cp.views), 0) AS total_views,
               COALESCE(SUM(cp.revenue), 0) AS total_revenue
        FROM content c
        LEFT JOIN content_performance cp ON cp.content_id = c.id
        GROUP BY c.category
        ORDER BY total_revenue DESC, total_views DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


def get_top_products(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT p.id, p.name, p.status, p.price, p.external_product_id,
               COALESCE(SUM(CASE WHEN o.status = 'COMPLETED' THEN o.amount ELSE 0 END), 0)
                   AS total_revenue,
               SUM(CASE WHEN o.status = 'COMPLETED' THEN 1 ELSE 0 END) AS completed_orders
        FROM products p
        LEFT JOIN orders o ON o.product_id = p.id
        GROUP BY p.id
        ORDER BY total_revenue DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


# -- Jobs (section 37, 40) -------------------------------------------------------


def get_failed_jobs(conn: sqlite3.Connection, limit: int = 10) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, job_type, attempt, started_at, finished_at, error "
        "FROM jobs WHERE status = 'FAILED' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def get_recent_jobs(conn: sqlite3.Connection, limit: int = 30) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, job_type, status, attempt, started_at, finished_at, error "
        "FROM jobs ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


# -- Products / opportunities (sections 23-27, reused by /products) --------------


def get_pending_approval_count(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM product_opportunities WHERE status = 'PENDING_APPROVAL'"
    ).fetchone()[0]


def get_products(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM products ORDER BY id DESC").fetchall()


def get_opportunities(conn: sqlite3.Connection, status: str | None = None) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT * FROM product_opportunities WHERE status = ? ORDER BY id DESC", (status,)
        ).fetchall()
    return conn.execute("SELECT * FROM product_opportunities ORDER BY id DESC").fetchall()


# -- Content list (/content page) ------------------------------------------------


def get_content_list(
    conn: sqlite3.Connection, status: str | None = None, limit: int = 50
) -> list[sqlite3.Row]:
    if status:
        return conn.execute(
            "SELECT id, topic, category, status, quality_score, opportunity_score, "
            "created_at, scheduled_at, published_at FROM content "
            "WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    return conn.execute(
        "SELECT id, topic, category, status, quality_score, opportunity_score, "
        "created_at, scheduled_at, published_at FROM content ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()


# -- Analytics list (/analytics page) --------------------------------------------


def get_content_performance(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT cp.id, c.id AS content_id, c.topic, c.category, pl.name AS platform,
               cp.views, cp.engagement_rate, cp.click_rate, cp.conversion_rate,
               cp.revenue, cp.revenue_per_1000_views, cp.winner_score, cp.collected_at
        FROM content_performance cp
        JOIN content c ON c.id = cp.content_id
        LEFT JOIN platforms pl ON pl.id = cp.platform_id
        ORDER BY cp.collected_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


# -- Owner view / system status (section 41) -------------------------------------


@dataclass
class SystemStatus:
    attention_items: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.attention_items


def get_system_status(conn: sqlite3.Connection, config: Config, db_path: str) -> SystemStatus:
    """Everything that could make the owner's answer to section 41's
    question - "do I need to do anything?" - be yes. Deliberately built
    only from signals this system actually tracks (health check issues,
    queue depth, pending product approvals, recent job failures) rather
    than CLAUDE.md section 41's own illustrative example ("Buffer
    authentication expires in 4 days") - GhostCash has no way to learn a
    Buffer token's expiry from Buffer's API, so faking that check would
    violate the project's rule against placeholder functionality that
    pretends to work."""
    items: list[str] = list(check_system(config, db_path).issues)

    queue_status = get_queue_status(conn, config)
    if queue_status.is_below_alert_threshold:
        items.append(
            f"Content queue is critically low: {queue_status.days_remaining:.1f} day(s) "
            f"remaining (alert threshold: {queue_status.alert_threshold_days}d). Run "
            "`ghostcash generate` and `ghostcash render` soon."
        )
    elif queue_status.is_below_emergency_threshold:
        items.append(
            f"Content queue is below target: {queue_status.days_remaining:.1f} day(s) "
            f"remaining (target: {queue_status.target_days}d). Consider running "
            "`ghostcash generate`."
        )

    pending = get_pending_approval_count(conn)
    if pending:
        noun = "opportunity" if pending == 1 else "opportunities"
        items.append(f"{pending} product {noun} awaiting approval - run `ghostcash products list`.")

    recent_failed = get_failed_jobs(conn, limit=5)
    if recent_failed:
        items.append(
            f"{len(recent_failed)} recent failed job(s) - see the Jobs page or `ghostcash health`."
        )

    return SystemStatus(attention_items=items)
