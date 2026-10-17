"""Content-performance statistics by dimension (CLAUDE.md section 31:
"maintain statistics by: category, topic, hook type, video template,
CTA, content length, posting time, platform, product. Use these
statistics to improve future content selection.").

Built from every content_performance row that already has a Winner
Score (i.e. `ghostcash analytics` has evaluated it - see
analytics/collector.py:evaluate_winners), joined back to the content
item it measures and, where available, the published_posts row that
tells us which platform and when.

Of the eight named dimensions, only CATEGORY/HOOK_TYPE/TEMPLATE are
currently fed back into a learned weight that changes future generation
(see optimization/weights.py) - CLAUDE.md section 50's weekly loop
diagram names exactly these three ("update category weights" / "update
hook weights" / "update template weights"). The rest (TOPIC, CTA_TYPE,
CONTENT_LENGTH, POSTING_HOUR, PLATFORM, PRODUCT) are computed and
returned here for observability (the `ghostcash optimize` report and,
later, the dashboard) - real statistics, just not yet wired into an
automated decision. That is a deliberate scope choice, not a gap: wiring
every one of the eight into live weighting would mean guessing at
feedback mechanisms CLAUDE.md never actually specifies.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from ghostcash.optimization.classify import classify_cta, classify_hook

DIMENSIONS: tuple[str, ...] = (
    "CATEGORY",
    "TOPIC",
    "HOOK_TYPE",
    "TEMPLATE",
    "CTA_TYPE",
    "CONTENT_LENGTH",
    "POSTING_HOUR",
    "PLATFORM",
    "PRODUCT",
)


@dataclass
class DimensionStat:
    dimension: str
    value: str
    sample_size: int
    avg_winner_score: float
    avg_engagement_rate: float | None
    avg_revenue_per_1000_views: float | None


def _length_bucket(seconds: float | None) -> str:
    if seconds is None:
        return "UNKNOWN"
    if seconds < 20:
        return "<20s"
    if seconds <= 30:
        return "20-30s"
    return ">30s"


def _posting_hour(published_at: str | None) -> str:
    if not published_at:
        return "UNKNOWN"
    try:
        hour = datetime.fromisoformat(published_at.replace("Z", "+00:00")).hour
    except ValueError:
        return "UNKNOWN"
    return f"{hour:02d}:00"


def _measured_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """One row per (content, platform) measurement that has a Winner
    Score - each content_performance row already carries its own
    winner_score, engagement_rate, and revenue_per_1000_views once
    evaluate_winners() has run (it writes those per-platform, not just
    on the aggregate), and its own platform_id, so PLATFORM and
    POSTING_HOUR naturally fall out per-row rather than needing a
    separate aggregation pass."""
    return conn.execute(
        """
        SELECT
            cp.content_id, cp.platform_id, cp.winner_score,
            cp.engagement_rate, cp.revenue_per_1000_views,
            c.category, c.topic, c.hook, c.cta, c.content_type,
            c.product_id, c.rendered_duration_seconds,
            pp.published_at,
            pl.name AS platform_name
        FROM content_performance cp
        JOIN content c ON c.id = cp.content_id
        LEFT JOIN published_posts pp
            ON pp.content_id = cp.content_id AND pp.platform_id = cp.platform_id
        LEFT JOIN platforms pl ON pl.id = cp.platform_id
        WHERE cp.winner_score IS NOT NULL
        """
    ).fetchall()


def _dimension_value(dimension: str, row: sqlite3.Row) -> str:
    if dimension == "CATEGORY":
        return row["category"] or "UNKNOWN"
    if dimension == "TOPIC":
        return row["topic"] or "UNKNOWN"
    if dimension == "HOOK_TYPE":
        return classify_hook(row["hook"])
    if dimension == "TEMPLATE":
        return row["content_type"] or "UNKNOWN"
    if dimension == "CTA_TYPE":
        return classify_cta(row["cta"])
    if dimension == "CONTENT_LENGTH":
        return _length_bucket(row["rendered_duration_seconds"])
    if dimension == "POSTING_HOUR":
        return _posting_hour(row["published_at"])
    if dimension == "PLATFORM":
        return row["platform_name"] or "UNKNOWN"
    if dimension == "PRODUCT":
        return str(row["product_id"]) if row["product_id"] is not None else "NONE"
    raise ValueError(f"Unknown dimension: {dimension!r}")  # pragma: no cover - exhaustive above


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def compute_statistics(conn: sqlite3.Connection) -> dict[str, list[DimensionStat]]:
    """Aggregate every measured (content, platform) row into
    per-dimension, per-value statistics. Returns {} for a dimension with
    no measured data yet (not an error - a fresh system just hasn't
    published/measured anything)."""
    rows = _measured_rows(conn)
    result: dict[str, list[DimensionStat]] = {dim: [] for dim in DIMENSIONS}
    if not rows:
        return result

    for dimension in DIMENSIONS:
        buckets: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            value = _dimension_value(dimension, row)
            buckets.setdefault(value, []).append(row)

        stats: list[DimensionStat] = []
        for value, bucket_rows in buckets.items():
            winner_scores = [r["winner_score"] for r in bucket_rows]
            engagement = [
                r["engagement_rate"] for r in bucket_rows if r["engagement_rate"] is not None
            ]
            revenue = [
                r["revenue_per_1000_views"]
                for r in bucket_rows
                if r["revenue_per_1000_views"] is not None
            ]
            stats.append(
                DimensionStat(
                    dimension=dimension,
                    value=value,
                    sample_size=len(bucket_rows),
                    avg_winner_score=_mean(winner_scores),
                    avg_engagement_rate=_mean(engagement) if engagement else None,
                    avg_revenue_per_1000_views=_mean(revenue) if revenue else None,
                )
            )
        stats.sort(key=lambda s: s.avg_winner_score, reverse=True)
        result[dimension] = stats

    return result


def compute_performance_multipliers(
    stats: list[DimensionStat],
    min_sample_size: int,
    clip_min: float = 0.5,
    clip_max: float = 2.0,
) -> dict[str, float]:
    """Turn a dimension's stats into {value: multiplier} observations for
    this run, where 1.0 = "performs like the sample-weighted average".

    This is a practical performance-proportional heuristic, not a claim
    of statistical rigor - appropriate for an MVP's content volume,
    same spirit as analytics/winner.py's own normalization approach.
    Values with fewer than `min_sample_size` measurements are left out
    entirely (their caller - optimization/weights.py - then keeps
    whatever weight was already learned for them, rather than snapping
    back to 1.0 on a single noisy data point). The multiplier is clipped
    to [clip_min, clip_max] so one outlier can't send a weight to zero
    or send it to dominate every future selection.
    """
    eligible = [s for s in stats if s.sample_size >= min_sample_size]
    if not eligible:
        return {}

    total_samples = sum(s.sample_size for s in eligible)
    overall_mean = sum(s.avg_winner_score * s.sample_size for s in eligible) / total_samples
    if overall_mean <= 0:
        return {}

    return {
        s.value: max(clip_min, min(clip_max, s.avg_winner_score / overall_mean)) for s in eligible
    }
