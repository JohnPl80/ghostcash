"""Analytics collection and winner evaluation (CLAUDE.md sections 28-29).

`collect_analytics()` is the "collect where APIs permit" half of
section 28: it walks published_posts rows that still need tracking,
asks Buffer for each one's current status/metrics, confirms posts that
have actually gone out (DRAFT/SCHEDULED -> PUBLISHED), and upserts
per-platform rows into content_performance.

`evaluate_winners()` is section 29: for every content item with at
least one content_performance row, aggregate across platforms, compute
a Winner Score, record an analytics_snapshots row, and flip status to
WINNER once the score clears the configured threshold.

Both are intentionally separate, idempotent steps (CLAUDE.md section 57)
rather than one monolithic pass, so `ghostcash analytics` can be re-run
safely - collecting metrics twice just re-upserts the same row, and
evaluating winners twice just recomputes the same score.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime

from ghostcash.analytics.metrics import (
    NormalizedMetrics,
    click_rate,
    conversion_rate,
    engagement_rate,
    normalize_buffer_metrics,
    revenue_per_1000_views,
    revenue_per_view,
)
from ghostcash.analytics.winner import (
    WinnerScoreResult,
    compute_winner_score,
    normalize_component_value,
)
from ghostcash.config.schema import Config
from ghostcash.publishing.base import PostNotFoundError
from ghostcash.publishing.buffer import BufferPublisher

# published_posts rows in these statuses still need checking: DRAFT/
# SCHEDULED might have gone out since the last run, and PUBLISHED posts
# keep accumulating metrics over time. PENDING/FAILED have no
# external_post_id yet, so there is nothing to ask Buffer about.
_TRACKABLE_STATUSES: tuple[str, ...] = ("SCHEDULED", "DRAFT", "PUBLISHED")


@dataclass
class CollectionResult:
    posts_checked: int = 0
    posts_marked_sent: int = 0
    metrics_collected: int = 0
    # Tracked posts Buffer no longer has (typically drafts the owner
    # deleted in Buffer to reject them). Skipped, never an error.
    posts_missing: int = 0


def select_trackable_published_posts(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    placeholders = ", ".join("?" for _ in _TRACKABLE_STATUSES)
    return conn.execute(
        f"SELECT id, content_id, platform_id, external_post_id, status "
        f"FROM published_posts "
        f"WHERE status IN ({placeholders}) AND external_post_id IS NOT NULL",
        _TRACKABLE_STATUSES,
    ).fetchall()


def collect_analytics(publisher: BufferPublisher, conn: sqlite3.Connection) -> CollectionResult:
    rows = select_trackable_published_posts(conn)
    result = CollectionResult()

    for row in rows:
        result.posts_checked += 1
        try:
            post = publisher.get_post_status_and_metrics(row["external_post_id"])
        except PostNotFoundError:
            result.posts_missing += 1
            continue

        newly_sent = (
            row["status"] in ("SCHEDULED", "DRAFT")
            and str(post.get("status", "")).lower() == "sent"
        )
        if newly_sent:
            conn.execute(
                "UPDATE published_posts SET status = 'PUBLISHED', published_at = ? WHERE id = ?",
                (post.get("sentAt"), row["id"]),
            )
            result.posts_marked_sent += 1

        raw_metrics = post.get("metrics") or []
        if not raw_metrics:
            # Buffer hasn't collected metrics for this post yet (common
            # right after it's sent - metricsUpdatedAt would be null).
            # Nothing to upsert; not an error.
            continue

        normalized = normalize_buffer_metrics(raw_metrics)
        _upsert_content_performance(conn, row["content_id"], row["platform_id"], normalized)
        result.metrics_collected += 1

    conn.commit()
    return result


def _upsert_content_performance(
    conn: sqlite3.Connection,
    content_id: int,
    platform_id: int | None,
    metrics: NormalizedMetrics,
) -> None:
    existing = conn.execute(
        "SELECT id FROM content_performance WHERE content_id = ? AND platform_id = ?",
        (content_id, platform_id),
    ).fetchone()

    eng_rate = engagement_rate(metrics)
    clk_rate = click_rate(metrics)
    conv_rate = conversion_rate(metrics)
    rev_per_view = revenue_per_view(metrics)
    rev_per_1000 = revenue_per_1000_views(metrics)
    now = datetime.now(UTC).isoformat()

    if existing is not None:
        conn.execute(
            """
            UPDATE content_performance SET
                views = ?, likes = ?, comments = ?, shares = ?,
                clicks = ?, profile_visits = ?, sales = ?, revenue = ?,
                engagement_rate = ?, click_rate = ?, conversion_rate = ?,
                revenue_per_view = ?, revenue_per_1000_views = ?,
                collected_at = ?
            WHERE id = ?
            """,
            (
                metrics.views,
                metrics.likes,
                metrics.comments,
                metrics.shares,
                metrics.clicks,
                metrics.profile_visits,
                metrics.sales,
                metrics.revenue,
                eng_rate,
                clk_rate,
                conv_rate,
                rev_per_view,
                rev_per_1000,
                now,
                existing["id"],
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO content_performance (
                content_id, platform_id, views, likes, comments, shares,
                clicks, profile_visits, sales, revenue,
                engagement_rate, click_rate, conversion_rate,
                revenue_per_view, revenue_per_1000_views, collected_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id,
                platform_id,
                metrics.views,
                metrics.likes,
                metrics.comments,
                metrics.shares,
                metrics.clicks,
                metrics.profile_visits,
                metrics.sales,
                metrics.revenue,
                eng_rate,
                clk_rate,
                conv_rate,
                rev_per_view,
                rev_per_1000,
                now,
            ),
        )


def _aggregate_metrics(rows: list[sqlite3.Row]) -> NormalizedMetrics:
    """Sum content_performance rows (one per platform) for a single
    content item into one NormalizedMetrics.

    clicks/profile_visits: summed only across rows where the value is
    NOT None, and the aggregate itself is None if every row is None -
    preserving "nobody has this data" vs "it's a real zero" even after
    aggregating across platforms.
    """
    views = sum(row["views"] or 0 for row in rows)
    likes = sum(row["likes"] or 0 for row in rows)
    comments = sum(row["comments"] or 0 for row in rows)
    shares = sum(row["shares"] or 0 for row in rows)
    sales = sum(row["sales"] or 0 for row in rows)
    revenue = sum(row["revenue"] or 0.0 for row in rows)

    click_values = [row["clicks"] for row in rows if row["clicks"] is not None]
    clicks = sum(click_values) if click_values else None
    visit_values = [row["profile_visits"] for row in rows if row["profile_visits"] is not None]
    profile_visits = sum(visit_values) if visit_values else None

    return NormalizedMetrics(
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        clicks=clicks,
        profile_visits=profile_visits,
        sales=sales,
        revenue=revenue,
    )


def _build_components(
    metrics: NormalizedMetrics, config: Config, has_product: bool
) -> dict[str, float]:
    """Decide which Winner Score components can actually be computed for
    this content item, each normalized to [0, 1].

    - engagement / click_through: included whenever their rate is
      computable (i.e. views > 0 and, for click_through, clicks is not
      None - which today means never, since Buffer exposes no click
      metric; see analytics/metrics.py).
    - revenue: only included if has_product is True. Revenue being 0
      with NO product attached means "no revenue tracking exists for
      this content", not "this content earned zero" - counting it as 0
      would wrongly punish content that was simply never monetized.
    - retention: not computed yet. Buffer's totalTimeWatched/viewers
      metrics would be the input, but GhostCash has no per-video
      duration baseline to normalize them against yet, and not every
      platform reports them. Left out (and its weight redistributed)
      rather than faked.
    """
    components: dict[str, float] = {}

    eng_rate = engagement_rate(metrics)
    if eng_rate is not None:
        components["engagement"] = normalize_component_value(
            eng_rate, config.analytics.target_engagement_rate
        )

    clk_rate = click_rate(metrics)
    if clk_rate is not None:
        components["click_through"] = normalize_component_value(
            clk_rate, config.analytics.target_click_rate
        )

    if has_product:
        rev_per_1000 = revenue_per_1000_views(metrics) or 0.0
        components["revenue"] = normalize_component_value(
            rev_per_1000, config.analytics.target_revenue_per_1000_views
        )

    return components


def evaluate_winners(conn: sqlite3.Connection, config: Config) -> dict[int, WinnerScoreResult]:
    """Compute a Winner Score for every content item that has at least
    one content_performance row, record an analytics_snapshots row for
    each, and promote content to WINNER status once its score clears
    config.analytics.winner_score_threshold.

    Returns {content_id: WinnerScoreResult} for everything evaluated.
    """
    content_ids = [
        row["content_id"]
        for row in conn.execute("SELECT DISTINCT content_id FROM content_performance").fetchall()
    ]

    results: dict[int, WinnerScoreResult] = {}
    for content_id in content_ids:
        perf_rows = conn.execute(
            "SELECT * FROM content_performance WHERE content_id = ?", (content_id,)
        ).fetchall()
        if not perf_rows:
            continue

        content_row = conn.execute(
            "SELECT product_id, status FROM content WHERE id = ?", (content_id,)
        ).fetchone()
        if content_row is None:
            continue
        has_product = content_row["product_id"] is not None

        aggregated = _aggregate_metrics(perf_rows)
        components = _build_components(aggregated, config, has_product)
        result = compute_winner_score(components, config.analytics.winner_weights)
        results[content_id] = result

        _write_content_snapshot(conn, content_id, aggregated)

        if (
            result.score >= config.analytics.winner_score_threshold
            and content_row["status"] != "FAILED"
        ):
            conn.execute("UPDATE content SET status = 'WINNER' WHERE id = ?", (content_id,))

        # Keep each platform row's own winner_score current too, so
        # per-platform comparisons (which hook/template/platform wins)
        # stay possible alongside the aggregated figure used for
        # promotion to WINNER status.
        for perf_row in perf_rows:
            row_metrics = NormalizedMetrics(
                views=perf_row["views"] or 0,
                likes=perf_row["likes"] or 0,
                comments=perf_row["comments"] or 0,
                shares=perf_row["shares"] or 0,
                clicks=perf_row["clicks"],
                profile_visits=perf_row["profile_visits"],
                sales=perf_row["sales"] or 0,
                revenue=perf_row["revenue"] or 0.0,
            )
            row_components = _build_components(row_metrics, config, has_product)
            row_result = compute_winner_score(row_components, config.analytics.winner_weights)
            conn.execute(
                "UPDATE content_performance SET winner_score = ? WHERE id = ?",
                (row_result.score, perf_row["id"]),
            )

    conn.commit()
    return results


def _write_content_snapshot(
    conn: sqlite3.Connection, content_id: int, metrics: NormalizedMetrics
) -> None:
    today = datetime.now(UTC).date().isoformat()
    conn.execute(
        """
        INSERT INTO analytics_snapshots (
            entity_type, entity_id, metric_date, views, likes, comments,
            shares, clicks, profile_visits, sales, revenue,
            engagement_rate, click_rate, conversion_rate,
            revenue_per_view, revenue_per_1000_views
        ) VALUES ('CONTENT', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content_id,
            today,
            metrics.views,
            metrics.likes,
            metrics.comments,
            metrics.shares,
            metrics.clicks,
            metrics.profile_visits,
            metrics.sales,
            metrics.revenue,
            engagement_rate(metrics),
            click_rate(metrics),
            conversion_rate(metrics),
            revenue_per_view(metrics),
            revenue_per_1000_views(metrics),
        ),
    )
