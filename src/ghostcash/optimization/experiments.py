"""Experiment evaluation (CLAUDE.md section 51).

Judges PENDING experiments - today, always one of
optimization/replication.py's hook A/B tests, see that module - by
practical margin once both arms have enough traffic, or closes an
experiment out as INCONCLUSIVE after a timeout. This project's content
volume is far too low for real statistical-significance testing to be
meaningful, so "practical margin" is the honest standard applied here,
not a weaker stand-in for rigor this system doesn't actually have.

`metric_name` is always `engagement_rate` today (set by
replication.py): Buffer exposes no click-through metric at all
(analytics/metrics.py), so this reuses the same engagement-rate
substitution already established for Winner Score (analytics/winner.py)
and product-opportunity detection (products/opportunities.py) rather
than inventing a different placeholder here.

The `experiments` table doubles as CLAUDE.md section 51's "maintain an
experiment log" requirement - there is no separate log to build.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ghostcash.analytics.metrics import NormalizedMetrics, engagement_rate
from ghostcash.config.schema import Config


@dataclass
class ExperimentEvaluation:
    experiment_key: str
    result: str  # CONTROL_WINS | VARIANT_WINS | INCONCLUSIVE
    decision: str
    control_value: float | None
    variant_value: float | None


def _iso(dt: datetime) -> str:
    """Same fixed-width ISO-8601 shape SQLite's own
    `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` column defaults produce
    (see dashboard/queries.py's identical helper) - comparing against a
    stored `created_at` is then a plain, correct string comparison
    instead of parsing it back into a datetime."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _aggregate_engagement(
    conn: sqlite3.Connection, content_id: int | None
) -> tuple[int, float | None]:
    """(total views, engagement_rate) for a content item summed across
    every platform it's been published to. (0, None) if there's no
    performance data yet, or content_id is None (e.g. the referenced
    content row was since deleted - ON DELETE SET NULL)."""
    if content_id is None:
        return 0, None
    rows = conn.execute(
        "SELECT views, likes, comments, shares FROM content_performance WHERE content_id = ?",
        (content_id,),
    ).fetchall()
    if not rows:
        return 0, None
    metrics = NormalizedMetrics(
        views=sum(row["views"] or 0 for row in rows),
        likes=sum(row["likes"] or 0 for row in rows),
        comments=sum(row["comments"] or 0 for row in rows),
        shares=sum(row["shares"] or 0 for row in rows),
    )
    return metrics.views, engagement_rate(metrics)


def _judge(
    control_views: int,
    control_value: float | None,
    variant_views: int,
    variant_value: float | None,
    min_views: int,
    threshold: float,
) -> tuple[str, str] | None:
    """None if not ready to judge yet (insufficient views on either
    side, or no engagement rate computable at all)."""
    if control_views < min_views or variant_views < min_views:
        return None
    if control_value is None or variant_value is None:
        return None

    if control_value <= 0 and variant_value <= 0:
        return "INCONCLUSIVE", "Neither arm has measurable engagement yet - inconclusive."

    base = control_value if control_value > 0 else variant_value
    diff = (variant_value - control_value) / base

    if diff >= threshold:
        return "VARIANT_WINS", (
            f"Variant hook outperformed the control by {diff * 100:.0f}% "
            f"({variant_value:.2f}% vs {control_value:.2f}% engagement) - "
            "worth adopting this hook style for similar content."
        )
    if diff <= -threshold:
        return "CONTROL_WINS", (
            f"Original hook outperformed the variant by {abs(diff) * 100:.0f}% "
            f"({control_value:.2f}% vs {variant_value:.2f}% engagement) - keep the "
            "original hook style."
        )
    return "INCONCLUSIVE", (
        f"Difference ({diff * 100:+.0f}%) is within the {threshold * 100:.0f}% "
        "practical-significance threshold - no clear winner."
    )


def _record_result(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    result: str,
    decision: str,
    control_value: float | None,
    variant_value: float | None,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE experiments SET status = 'EVALUATED', result = ?, decision = ?, "
        "control_value = ?, variant_value = ?, evaluated_at = ? WHERE id = ?",
        (result, decision, control_value, variant_value, now, row["id"]),
    )
    if row["content_variant_id"] is not None:
        variant_status = "WINNER" if result == "VARIANT_WINS" else "REJECTED"
        conn.execute(
            "UPDATE content_variants SET status = ? WHERE id = ?",
            (variant_status, row["content_variant_id"]),
        )
    conn.commit()


def evaluate_experiments(conn: sqlite3.Connection, config: Config) -> list[ExperimentEvaluation]:
    """Evaluate every PENDING experiment that's either ready to judge
    (both arms have cleared `min_views_for_experiment_evaluation`) or
    has run past `experiment_timeout_days` without getting there.

    Returns every experiment actually resolved this run (empty list if
    none were ready and none timed out).
    """
    opt = config.optimization
    timeout_cutoff = _iso(datetime.now(UTC) - timedelta(days=opt.experiment_timeout_days))
    rows = conn.execute("SELECT * FROM experiments WHERE status = 'PENDING'").fetchall()

    resolved: list[ExperimentEvaluation] = []
    for row in rows:
        control_views, control_value = _aggregate_engagement(conn, row["control_content_id"])
        variant_views, variant_value = _aggregate_engagement(conn, row["variant_content_id"])

        judged = _judge(
            control_views,
            control_value,
            variant_views,
            variant_value,
            opt.min_views_for_experiment_evaluation,
            opt.practical_significance_threshold,
        )

        if judged is None:
            if row["created_at"] >= timeout_cutoff:
                continue  # not ready yet, and hasn't timed out either
            judged = (
                "INCONCLUSIVE",
                f"Timed out after {opt.experiment_timeout_days} days without "
                f"{opt.min_views_for_experiment_evaluation} views on both arms "
                f"(control={control_views}, variant={variant_views}).",
            )

        result, decision = judged
        _record_result(conn, row, result, decision, control_value, variant_value)
        resolved.append(
            ExperimentEvaluation(
                experiment_key=row["experiment_key"],
                result=result,
                decision=decision,
                control_value=control_value,
                variant_value=variant_value,
            )
        )

    return resolved
