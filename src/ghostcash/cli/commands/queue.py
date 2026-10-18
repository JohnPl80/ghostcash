"""`ghostcash queue` (CLAUDE.md section 22, section 33 nightly step 3).

Reports the publishing queue's days-of-runway and the two configured
buffer thresholds. Exists as its own command (rather than folding this
into `ghostcash publish`) so GitHub Actions workflows can check queue
health *before* deciding whether to run an extra emergency
generate+render pass, and *again afterwards* to decide whether the
situation is actually still urgent - see publishing.yml.

Machine-readable integration for GitHub Actions, both best-effort and
backward compatible with running this locally:
  - If $GITHUB_OUTPUT is set, writes `below_emergency`, `below_alert`,
    `ready_count`, `scheduled_count`, and `days_remaining` so a workflow
    step can branch on them (`if: steps.queue.outputs.below_emergency
    == 'true'`).
  - If $GITHUB_STEP_SUMMARY is set, appends a short Markdown status
    line to the run's summary page.

Exit code is 0 unless the queue is below the *alert* threshold (section
22: "If queue falls below 1 day, send an alert"). Falling below the
*emergency* threshold alone is not treated as a failure here - that's
meant to be handled procedurally (trigger more generation in the same
run), not by failing a job. GhostCash has no NotificationProvider yet
(CLAUDE.md section 38, not built in any phase so far), so failing the
step is the real, zero-setup alert mechanism available today: GitHub
notifies repo watchers/owners of a failed workflow run by default. This
is deliberately not a fake notification integration - it is a genuine,
visible signal using infrastructure GhostCash already runs on.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.database.connection import get_connection
from ghostcash.publishing.queue import QueueStatus, get_queue_status


def _write_github_output(status: QueueStatus) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        return
    with Path(output_path).open("a", encoding="utf-8") as fh:
        fh.write(f"below_emergency={'true' if status.is_below_emergency_threshold else 'false'}\n")
        fh.write(f"below_alert={'true' if status.is_below_alert_threshold else 'false'}\n")
        fh.write(f"ready_count={status.ready_count}\n")
        fh.write(f"scheduled_count={status.scheduled_count}\n")
        fh.write(f"approved_count={status.approved_count}\n")
        fh.write(f"days_remaining={status.days_remaining:.2f}\n")


def _write_github_summary(status: QueueStatus, days_remaining: float) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    if status.is_below_alert_threshold:
        marker = "🔴 BELOW ALERT THRESHOLD"
    elif status.is_below_emergency_threshold:
        marker = "🟠 below emergency threshold"
    else:
        marker = "🟢 healthy"
    with Path(summary_path).open("a", encoding="utf-8") as fh:
        fh.write(
            f"\n**Publishing queue:** {marker} - {days_remaining:.1f} days remaining "
            f"({status.ready_count} ready, {status.scheduled_count} scheduled"
            f"{status.waiting_note}; "
            f"target {status.target_days}d, emergency <{status.emergency_threshold_days}d, "
            f"alert <{status.alert_threshold_days}d)\n"
        )


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    try:
        status = get_queue_status(conn, config)
    finally:
        conn.close()

    print(
        f"Queue: {status.ready_count} ready, {status.scheduled_count} scheduled"
        f"{status.waiting_note} "
        f"(~{status.days_remaining:.1f} days at {config.publishing.posts_per_day}/day)."
    )
    print(
        f"Targets: {status.target_days}d target, "
        f"emergency below {status.emergency_threshold_days}d, "
        f"alert below {status.alert_threshold_days}d."
    )

    _write_github_output(status)
    _write_github_summary(status, status.days_remaining)

    if status.is_below_alert_threshold:
        print(
            f"[alert] queue is below the {status.alert_threshold_days}-day alert threshold. "
            "No NotificationProvider is wired up yet (section 38) - this command exits "
            "non-zero specifically so a GitHub Actions run fails and GitHub's own "
            "workflow-failure notification reaches you."
        )
        return 1
    if status.is_below_emergency_threshold:
        print(
            f"[warn] queue is below the {status.emergency_threshold_days}-day emergency "
            "threshold - consider running `ghostcash generate` and `ghostcash render`."
        )
    else:
        print("[ok] queue is healthy.")
    return 0
