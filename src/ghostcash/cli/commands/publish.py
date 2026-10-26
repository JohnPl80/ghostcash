"""`ghostcash publish` (CLAUDE.md sections 19-22).

Syncs channels from Buffer, then publishes READY content to every
active platform: a Buffer draft if human_approval_mode.publishing is
APPROVAL_REQUIRED (the default, section 52), or actually scheduled if
AUTONOMOUS. Per-platform and per-content failures are recorded and
reported, not fatal - see publishing/pipeline.py.
"""

from __future__ import annotations

import argparse
import traceback

from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.buffer_queue import make_queue_counter
from ghostcash.publishing.channels import resolve_organization_id, sync_channels
from ghostcash.publishing.factory import get_media_host, get_publisher
from ghostcash.publishing.media_host import MediaHostError
from ghostcash.publishing.pipeline import (
    QUEUE_UNREADABLE_PREFIX,
    PublishingSetupError,
    publish_batch,
)
from ghostcash.publishing.queue import get_queue_status


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    missing = validate_environment(["publishing"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash publish`.")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    job_id = start_job(conn, "publish_content")

    try:
        publisher = get_publisher(config)
        media_host = get_media_host(config)
        if not isinstance(publisher, BufferPublisher):
            raise PublisherError(
                "Channel sync and organization resolution are Buffer-specific; the "
                "configured publisher isn't a BufferPublisher."
            )
        organization_id = resolve_organization_id(publisher, config)
        sync_result = sync_channels(publisher, conn, organization_id)
    except (PublisherError, MediaHostError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] {exc}")
        return 1

    if sync_result.synced:
        print(f"Synced channels: {', '.join(sorted(set(sync_result.synced)))}")
    if sync_result.skipped_services:
        print(
            f"Skipped Buffer channels outside GhostCash's scope: "
            f"{', '.join(sorted(set(sync_result.skipped_services)))}"
        )

    notes: list[str] = []
    try:
        outcomes = publish_batch(
            publisher,
            media_host,
            conn,
            config,
            count=args.count,
            queue_counter=make_queue_counter(publisher, organization_id),
            notes=notes,
        )
    except PublishingSetupError as exc:
        succeed_job(conn, job_id)  # not a failure - just nothing to do yet
        conn.close()
        print(f"{exc}")
        return 0
    except Exception as exc:  # unexpected - record it, then let it surface
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        raise

    succeeded = [o for o in outcomes if o.result is not None]
    failed = [o for o in outcomes if o.result is None]
    queue_unreadable = any(note.startswith(QUEUE_UNREADABLE_PREFIX) for note in notes)

    if queue_unreadable:
        fail_job(conn, job_id, "; ".join(notes))
    elif succeeded or not failed:
        succeed_job(conn, job_id)
    else:
        fail_job(conn, job_id, f"All {len(failed)} publish attempt(s) failed.")

    queue_status = get_queue_status(conn, config)
    conn.close()

    for note in notes:
        print(note)

    if not outcomes:
        if not notes:
            print("No READY content is waiting to be published.")
    else:
        print(f"\nPublished {len(succeeded)} of {len(outcomes)} (content, platform) attempt(s).")
        for outcome in outcomes:
            status = outcome.result.status if outcome.result else "FAILED"
            marker = f"[{status:9s}]"
            print(f"  {marker} #{outcome.content.id} -> {outcome.platform.name}")
        for outcome in failed:
            print(
                f"           ...#{outcome.content.id} -> {outcome.platform.name}: {outcome.error}"
            )

    print(
        f"\nQueue: {queue_status.ready_count} ready, {queue_status.scheduled_count} scheduled"
        f"{queue_status.waiting_note} "
        f"(~{queue_status.days_remaining:.1f} days at {config.publishing.posts_per_day}/day, "
        f"target {queue_status.target_days} days)."
    )
    if queue_status.is_below_alert_threshold:
        print(
            f"[alert] queue is below the {queue_status.alert_threshold_days}-day alert "
            "threshold - notifications aren't wired up yet (Phase 2+), so this is "
            "informational only for now."
        )
    elif queue_status.is_below_emergency_threshold:
        print(
            f"[warn] queue is below the {queue_status.emergency_threshold_days}-day emergency "
            "threshold - consider running `ghostcash generate` and `ghostcash render`."
        )

    if queue_unreadable:
        return 1
    return 0 if succeeded or not failed else 1
