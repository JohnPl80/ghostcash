"""`ghostcash drafts discard` - clear unwanted drafts out of Buffer.

  ghostcash drafts discard                 preview the oldest video (deletes nothing)
  ghostcash drafts discard --limit 10      preview the 10 oldest videos
  ghostcash drafts discard --limit 1 --apply
                                           really delete that one video's drafts
  ghostcash drafts discard --all --apply   delete every draft video
  ghostcash drafts discard --created-before 2026-10-23 --all --apply
                                           only videos made before that day

Without `--apply` nothing is deleted and Buffer is not even contacted.
Details and safety rules: ghostcash/publishing/discard.py.
"""

from __future__ import annotations

import argparse
import traceback
from datetime import date

from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.discard import (
    DiscardCandidate,
    discard_drafts,
    select_discardable_drafts,
)
from ghostcash.publishing.factory import get_publisher


def run(args: argparse.Namespace) -> int:
    if args.drafts_action != "discard":
        print(f"[error] unknown drafts action: {args.drafts_action!r}")
        return 2
    return _discard(args)


def _discard(args: argparse.Namespace) -> int:
    limit: int | None = None if args.all else args.limit
    if limit is not None and limit < 1:
        print("[error] --limit must be 1 or more.")
        return 1

    created_before: str | None = args.created_before
    if created_before:
        try:
            date.fromisoformat(created_before)
        except ValueError:
            print(f"[error] --created-before must look like 2026-10-23, not {created_before!r}.")
            return 1

    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    try:
        everything = select_discardable_drafts(conn, created_before=created_before)
        if not everything:
            print("No videos are waiting as drafts - nothing to discard.")
            return 0
        selected = everything if limit is None else everything[:limit]
        remaining_after = len(everything) - len(selected)

        _print_selection(selected, len(everything))

        if not args.apply:
            print()
            print("Preview only - nothing was deleted and Buffer was not contacted.")
            print("Add --apply to delete the drafts listed above from Buffer.")
            return 0

        missing = validate_environment(["publishing"])
        if missing:
            print(f"[error] missing required environment variable(s): {', '.join(missing)}")
            return 1

        job_id = start_job(conn, "discard_drafts")
        try:
            publisher = get_publisher(config)
            if not isinstance(publisher, BufferPublisher):
                raise PublisherError("Discarding drafts is Buffer-specific today.")
            result = discard_drafts(conn, publisher, selected)
        except PublisherError as exc:
            fail_job(conn, job_id, str(exc), traceback.format_exc())
            print(f"[error] {exc}")
            return 1
        except Exception as exc:  # unexpected - record it, then let it surface
            fail_job(conn, job_id, str(exc), traceback.format_exc())
            raise

        if result.failures and not result.discarded:
            fail_job(conn, job_id, f"{len(result.failures)} video(s) could not be discarded.")
        else:
            succeed_job(conn, job_id)

        print()
        print(
            f"Discarded {len(result.discarded)} video(s): removed {result.posts_deleted} "
            f"draft(s) from Buffer ({result.posts_already_gone} were already gone)."
        )
        for content_id in result.discarded:
            print(f"  [REJECTED] #{content_id}")
        for content_id, reason in result.failures:
            print(f"  [FAILED]   #{content_id}: {reason}")
        if result.stopped_early:
            print("Stopped early after repeated failures - fix the problem above, then run again.")

        not_done = len(selected) - len(result.discarded)
        if result.discarded and not result.failures:
            if remaining_after:
                print(
                    f"{remaining_after} more video(s) are still waiting as drafts. "
                    "Check Buffer, then run again with a higher --limit (or --all)."
                )
            else:
                print("No more videos are waiting as drafts.")
        elif not_done and not result.failures:
            print(f"{not_done} video(s) were not processed.")
        return 1 if result.failures else 0
    finally:
        conn.close()


def _print_selection(selected: list[DiscardCandidate], total: int) -> None:
    print(f"{total} video(s) are waiting as drafts in Buffer.")
    print(f"This run covers {len(selected)} of them (oldest first):")
    for candidate in selected:
        platforms = ", ".join(post.platform for post in candidate.posts)
        print(
            f"  #{candidate.content_id} {candidate.title!r} "
            f"(made {candidate.created_at[:10]}) - {len(candidate.posts)} draft(s): {platforms}"
        )
