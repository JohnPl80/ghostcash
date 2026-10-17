"""`ghostcash render` (CLAUDE.md sections 16-18).

Renders the best available APPROVED-but-unrendered content into MP4
files via ghostcash.video.render_batch(). Tracked as a single job;
per-item failures are recorded on the content row itself (status
FAILED) rather than aborting the whole batch, mirroring the resilience
pattern already used by `ghostcash generate`.
"""

from __future__ import annotations

import argparse
import traceback

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.video.renderer import RenderError, get_tts_provider, render_batch
from ghostcash.video.tts import TTSError, is_flite_available


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    if not is_flite_available(config.video.ffmpeg_binary):
        print(
            "[error] ffmpeg does not appear to support the 'flite' filter needed for "
            "local TTS. The standard Ubuntu/Debian ffmpeg package includes it; a "
            "minimal custom build may not."
        )
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    job_id = start_job(conn, "render_videos")

    try:
        tts_provider = get_tts_provider(config)
        outcomes = render_batch(conn, config, count=args.count, tts_provider=tts_provider)
    except (RenderError, TTSError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] render setup failed: {exc}")
        return 1
    except Exception as exc:  # unexpected - record it, then let it surface
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        raise

    if not outcomes:
        succeed_job(conn, job_id)
        conn.close()
        print("No approved content is waiting to be rendered.")
        return 0

    succeeded = [c for c, result, _ in outcomes if result is not None]
    failed = [(c, error) for c, result, error in outcomes if result is None]

    if succeeded or not failed:
        succeed_job(conn, job_id)
    else:
        fail_job(conn, job_id, f"All {len(failed)} render attempt(s) failed.")
    conn.close()

    print(f"Rendered {len(succeeded)} of {len(outcomes)} content item(s).")
    for content, result, _ in outcomes:
        if result is not None:
            print(
                f"  [READY]  #{content.id} {content.title} -> "
                f"{result.output_path} ({result.duration_seconds:.1f}s)"
            )
    for content, error in failed:
        print(f"  [FAILED] #{content.id} {content.title}: {error}")

    return 0 if succeeded or not failed else 1
