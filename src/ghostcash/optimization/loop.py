"""The autonomous optimization cycle (CLAUDE.md sections 30-32, 50-51):

    COLLECT DATA -> ANALYZE -> IDENTIFY WINNERS -> IDENTIFY LOSERS ->
    UPDATE CATEGORY WEIGHTS -> UPDATE HOOK WEIGHTS -> UPDATE TEMPLATE
    WEIGHTS -> GENERATE NEW CONTENT

"Collect data / analyze / identify winners" is `ghostcash analytics`
(Phase 7) - this cycle assumes that has already run and reads what it
left behind (content_performance, Winner Scores) rather than re-polling
Buffer itself (section 35: avoid excessive API calls; section 33 also
already lists these as nightly's own steps 4-5, run once). "Identify
losers" has no separate step here: optimization/statistics.py's
performance-proportional multipliers already demote a poor-performing
value's influence continuously (clipped, not zeroed) rather than
needing a hard winner/loser split to act on.

"Generate new content" is winner replication
(optimization/replication.py) - new content items (and a controlled
experiment to test them) seeded directly from what's already proven to
work, which is the entire point of section 30's replication loop.

See cli/commands/optimize.py for the interval_days gate that keeps this
from literally re-running every single night - this function itself is
just the cycle's logic and runs unconditionally when called.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from ghostcash.ai.base import AIProvider
from ghostcash.ai.budget import UsageTracker
from ghostcash.config.schema import Config
from ghostcash.optimization.experiments import ExperimentEvaluation, evaluate_experiments
from ghostcash.optimization.replication import (
    ReplicationError,
    ReplicationResult,
    replicate_winners,
)
from ghostcash.optimization.statistics import (
    DimensionStat,
    compute_performance_multipliers,
    compute_statistics,
)
from ghostcash.optimization.weights import update_weights


@dataclass
class OptimizationReport:
    statistics: dict[str, list[DimensionStat]] = field(default_factory=dict)
    updated_weights: dict[str, dict[str, float]] = field(default_factory=dict)
    replications: list[ReplicationResult] = field(default_factory=list)
    # Why `replications` is empty, when it is for a reason worth surfacing
    # (no AI key configured, or the AI provider failed on every winner due)
    # - distinct from "nothing to do" (no winners were due at all).
    replication_skipped: str | None = None
    experiment_evaluations: list[ExperimentEvaluation] = field(default_factory=list)


def run_optimization_cycle(
    ai_provider: AIProvider | None,
    conn: sqlite3.Connection,
    config: Config,
    usage_tracker: UsageTracker | None = None,
) -> OptimizationReport:
    """Run one full optimization cycle and return a report of what
    changed. `ai_provider=None` (no AI_API_KEY configured) still runs
    the weight-update and experiment-evaluation halves, which need no
    AI access - only winner replication is skipped."""
    opt = config.optimization
    report = OptimizationReport()

    stats = compute_statistics(conn)
    report.statistics = stats

    category_observations = compute_performance_multipliers(stats["CATEGORY"], opt.min_sample_size)
    hook_type_observations = compute_performance_multipliers(
        stats["HOOK_TYPE"], opt.min_sample_size
    )
    template_observations = compute_performance_multipliers(stats["TEMPLATE"], opt.min_sample_size)
    report.updated_weights = update_weights(
        conn,
        category_observations=category_observations,
        hook_type_observations=hook_type_observations,
        template_observations=template_observations,
        alpha=opt.weight_smoothing_alpha,
    )

    if ai_provider is None:
        report.replication_skipped = "AI_API_KEY not set - skipping winner replication."
    else:
        try:
            report.replications = replicate_winners(ai_provider, conn, config, usage_tracker)
        except ReplicationError as exc:
            report.replication_skipped = str(exc)

    report.experiment_evaluations = evaluate_experiments(conn, config)

    return report
