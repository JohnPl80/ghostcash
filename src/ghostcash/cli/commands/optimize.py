"""`ghostcash optimize` (CLAUDE.md sections 30-32, 50-51).

Runs the autonomous optimization cycle: blends this run's category/
hook-type/template performance into the learned weights that bias
future topic/content generation (see content/topics.py and
content/generation.py), replicates proven winners into a controlled
hook A/B test (optimization/replication.py), and evaluates any
experiments that now have enough data (optimization/experiments.py).

Section 50 says "every week", but this command is self-gating rather
than relying on its caller to only invoke it weekly - mirroring
ghostcash/health.py's `max_job_age_hours` pattern: it checks when
`ghostcash optimize` last succeeded and skips cleanly (exit 0) if fewer
than `config.optimization.interval_days` have passed. That means
nightly.yml can simply call `ghostcash optimize` every night without
any scheduling logic of its own - most nights it's a fast no-op that
still costs zero AI calls.

Winner replication is the only part of the cycle that needs
AI_API_KEY - weight updates and experiment evaluation work from data
already in the database. Rather than hard-requiring the key up front
(which would block the parts of the cycle that don't need it), a
missing key is treated the same as every other optional integration
elsewhere in this project: reported plainly, and the rest of the run
proceeds (see optimization/loop.py's `ai_provider=None` path).
"""

from __future__ import annotations

import argparse
import traceback
from datetime import UTC, datetime

from ghostcash.ai.budget import UsageTracker
from ghostcash.ai.factory import get_ai_provider
from ghostcash.config.loader import ConfigError, load_config
from ghostcash.config.schema import Config
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.optimization.loop import OptimizationReport, run_optimization_cycle


def _days_since_last_success(conn) -> float | None:
    row = conn.execute(
        "SELECT finished_at FROM jobs WHERE job_type = 'optimize' AND status = 'SUCCEEDED' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None or row["finished_at"] is None:
        return None
    finished_at = datetime.fromisoformat(row["finished_at"])
    return (datetime.now(UTC) - finished_at).total_seconds() / 86400


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)

    age_days = _days_since_last_success(conn)
    interval = config.optimization.interval_days
    if age_days is not None and age_days < interval:
        print(
            f"[skip] last successful optimization run was {age_days:.1f}d ago - "
            f"running again in {interval - age_days:.1f}d (interval: {interval}d). "
            "Use --force to run anyway."
        )
        if not getattr(args, "force", False):
            conn.close()
            return 0
        print("[ok] --force given - running anyway.")

    job_id = start_job(conn, "optimize")
    provider = get_ai_provider(config.ai) if config.ai.api_key else None
    usage_tracker = UsageTracker(conn)

    try:
        report = run_optimization_cycle(provider, conn, config, usage_tracker)
    except Exception as exc:  # unexpected - record it, then let it surface
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        raise

    succeed_job(conn, job_id)
    _print_report(report, config)

    conn.close()
    return 0


def _print_report(report: OptimizationReport, config: Config) -> None:
    print("Learned weight updates (category / hook type / template):")
    for dimension, weights in report.updated_weights.items():
        if not weights:
            print(f"  {dimension}: no data yet")
            continue
        top = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:3]
        rendered = ", ".join(f"{value}={multiplier:.2f}" for value, multiplier in top)
        print(f"  {dimension}: {rendered}")

    if report.replications:
        print(f"\nReplicated {len(report.replications)} winner(s):")
        for result in report.replications:
            extra = (
                f" - testing content #{result.variant_content_id}"
                if result.variant_content_id is not None
                else ""
            )
            print(
                f"  content #{result.content_id}: {result.hooks_generated} hooks, "
                f"{result.scripts_generated} scripts, {result.ctas_generated} CTAs, "
                f"{result.topics_generated} related topics{extra}"
            )
    elif report.replication_skipped:
        print(f"\n[info] winner replication: {report.replication_skipped}")
    else:
        print("\nNo winners due for replication this run.")

    if report.experiment_evaluations:
        print(f"\nResolved {len(report.experiment_evaluations)} experiment(s):")
        for evaluation in report.experiment_evaluations:
            print(f"  {evaluation.experiment_key}: {evaluation.result} - {evaluation.decision}")
    else:
        print("\nNo experiments ready to evaluate this run.")
