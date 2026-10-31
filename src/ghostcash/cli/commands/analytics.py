"""`ghostcash analytics` (CLAUDE.md sections 28-29).

Collects current status/metrics from Buffer for every tracked
published_posts row, then evaluates Winner Score for every content item
that now has performance data. Analytics collection is Buffer-specific
today (same reasoning as `publish` needing a BufferPublisher for channel
sync) - a future second publishing provider would need its own metrics
path here.
"""

from __future__ import annotations

import argparse
import traceback

from ghostcash.analytics.collector import collect_analytics, evaluate_winners
from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.factory import get_publisher


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    missing = validate_environment(["publishing"], config)
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash analytics`.")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    job_id = start_job(conn, "collect_analytics")

    try:
        publisher = get_publisher(config)
        if not isinstance(publisher, BufferPublisher):
            raise PublisherError("Analytics collection is Buffer-specific today.")
        collection = collect_analytics(publisher, conn)
    except PublisherError as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] {exc}")
        return 1
    except Exception as exc:  # unexpected - record it, then let it surface
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        raise

    winner_results = evaluate_winners(conn, config)
    succeed_job(conn, job_id)

    print(
        f"Checked {collection.posts_checked} post(s): "
        f"{collection.posts_marked_sent} newly confirmed sent, "
        f"{collection.metrics_collected} with metrics collected."
    )
    if collection.posts_missing:
        print(
            f"{collection.posts_missing} tracked post(s) no longer exist in Buffer "
            "(deleted there) and were skipped."
        )
        if collection.contents_rejected:
            print(
                f"{collection.contents_rejected} content item(s) had every post deleted and are "
                "now marked REJECTED (they no longer count toward the queue)."
            )

    if winner_results:
        winners = conn.execute("SELECT id FROM content WHERE status = 'WINNER'").fetchall()
        print(f"\nEvaluated {len(winner_results)} content item(s) for Winner Score.")
        top = sorted(winner_results.items(), key=lambda kv: kv[1].score, reverse=True)[:5]
        for content_id, result in top:
            used = ", ".join(result.components_used) or "none"
            print(f"  #{content_id}: score={result.score:.2f} (using: {used})")
        print(f"Currently marked WINNER: {len(winners)} content item(s).")
    else:
        print("\nNo content with collected metrics yet - nothing to evaluate.")

    conn.close()
    return 0
