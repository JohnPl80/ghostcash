"""`ghostcash run` / `ghostcash run --dry-run` (CLAUDE.md sections 42-43,
62 - the project's own Definition of Done).

Orchestrates the full pipeline in one command, in the exact order
section 62 specifies:

    20 candidate topics -> 5 selected topics -> 5 scripts -> 5 videos ->
    5 validated posts -> publication schedule -> analytics records ->
    report

(The default counts - `content.topics_per_run: 20`, `content.
content_per_run: 5`, `video.render_per_run: 5` - already match section
43's own worked example; nothing special-cased here to produce those
numbers, they just fall out of the existing config defaults.)

`--dry-run` (section 43: "mandatory... before any external publishing")
runs every step that is genuinely local for real:

  - real AI calls to generate topics and scripts
  - real quality control and duplicate detection
  - real local ffmpeg rendering
  - real product-opportunity detection against whatever performance
    data already exists

but never calls Buffer, GitHub Releases, or Gumroad. The "publication
schedule" step is instead computed from what ended up READY in the
database, times `config.platforms` (the configured platform list,
section 21 - not a live Buffer channel sync, since dry-run must not
touch Buffer at all), with no network call and no credential
requirement beyond AI_API_KEY. That is what makes dry-run genuinely
safe to run immediately after `ghostcash init` plus an AI key, before
Buffer/Gumroad are even set up - matching section 63's "first live
test" workflow, where a dry run comes first. It's also why the
analytics step is skipped rather than faked in dry-run: nothing was
actually published this run for Buffer to have metrics on yet.

A real `ghostcash run` performs the identical sequence but calls
`ghostcash publish`'s actual Buffer channel sync + scheduling and
`ghostcash analytics`'s actual metrics collection for the schedule/
analytics steps (section 62: "must perform the same workflow with real
external integrations"). Per section 52, Buffer posts are still created
as DRAFTs by default (human_approval_mode.publishing), so "real" here
means real Buffer API calls, not that content is guaranteed to reach a
live audience unattended.

Tracked as a single job (`run_dry_run` / `run_live`) - granular,
resumable progress already exists one level down (topics/content rows,
rendered files, published_posts idempotency keys), so re-running `run`
after a partial failure naturally picks up wherever those left off,
the same as running the individual commands again would.
"""

from __future__ import annotations

import argparse
import sqlite3
import traceback

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.ai.factory import get_ai_provider
from ghostcash.analytics.collector import collect_analytics, evaluate_winners
from ghostcash.cli.commands.report import print_report
from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.config.schema import Config
from ghostcash.content.generation import (
    ContentGenerationError,
    generate_scripts,
    select_candidate_topics,
)
from ghostcash.content.topics import TopicGenerationError, generate_topics
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.products.opportunities import OpportunityDetectionError, detect_opportunities
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.channels import resolve_organization_id, sync_channels
from ghostcash.publishing.factory import get_media_host, get_publisher
from ghostcash.publishing.media_host import MediaHostError
from ghostcash.publishing.pipeline import PublishingSetupError, publish_batch, select_ready_content
from ghostcash.video.renderer import (
    RenderError,
    backup_voice_notice,
    deferred_notice,
    flite_required,
    get_tts_provider,
    render_batch,
)
from ghostcash.video.tts import TTSError, is_flite_available


def run(args: argparse.Namespace) -> int:
    dry_run = bool(getattr(args, "dry_run", False))

    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    missing = validate_environment(["ai"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash run`.")
        return 1

    if flite_required(config) and not is_flite_available(config.video.ffmpeg_binary):
        print(
            "[error] ffmpeg does not appear to support the 'flite' filter needed for "
            "local TTS. The standard Ubuntu/Debian ffmpeg package includes it; a "
            "minimal custom build may not. Checked before spending any AI calls."
        )
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    job_id = start_job(conn, "run_dry_run" if dry_run else "run_live")

    provider = get_ai_provider(config.ai)
    usage_tracker = UsageTracker(conn)

    topics_count = args.topics_count or config.content.topics_per_run
    content_count = args.content_count or config.content.content_per_run

    print(f"GhostCash end-to-end run ({'DRY RUN' if dry_run else 'LIVE'})")
    print("=" * 40)

    # 1. Topics (section 11).
    try:
        topics = generate_topics(provider, conn, config, count=topics_count)
    except (AIProviderError, TopicGenerationError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] topic generation failed: {exc}")
        return 1
    _record_usage(provider, usage_tracker)
    topics_generated = len(topics)

    # 2. Select the best candidates, then generate + quality-check
    # scripts for them (sections 12-15). select_candidate_topics() is
    # called here purely to report "Topics selected" - generate_scripts()
    # below re-runs the identical read internally to do the real work,
    # which is safe (it's a plain SELECT, nothing changed in between).
    topics_selected = len(select_candidate_topics(conn, content_count))
    try:
        content_items = generate_scripts(
            provider, conn, config, count=content_count, usage_tracker=usage_tracker
        )
    except (AIProviderError, ContentGenerationError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] content generation failed: {exc}")
        return 1
    scripts_generated = len(content_items)

    # 3. Render approved scripts to video (sections 16-18).
    try:
        tts_provider = get_tts_provider(config)
        render_outcomes = render_batch(conn, config, count=content_count, tts_provider=tts_provider)
    except (RenderError, TTSError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] rendering failed: {exc}")
        return 1
    videos_rendered = len([outcome for outcome in render_outcomes if outcome[1] is not None])
    voice_notice = backup_voice_notice(tts_provider)
    if voice_notice:
        print(voice_notice)
    waiting_notice = deferred_notice(render_outcomes)
    if waiting_notice:
        print(waiting_notice)

    # 4. Publication schedule (sections 19-22) - real Buffer scheduling
    # in a live run, a pure local count (no network call) in dry-run.
    if dry_run:
        ready_items = select_ready_content(conn, limit=max(content_count, 1))
        platform_count = len(config.platforms) or 1
        posts_count = len(ready_items) * platform_count
        schedule_label = "Posts that WOULD be scheduled"
    else:
        posts_count, schedule_note = _publish_for_real(conn, config, content_count)
        schedule_label = "Posts scheduled"
        if schedule_note:
            print(schedule_note)

    # 5. Product opportunity detection (sections 23-25) - local DB only
    # (an opportunity still requires human approval before anything is
    # sold, section 24), so this runs identically in both modes.
    try:
        opportunities = detect_opportunities(provider, conn, config)
    except OpportunityDetectionError as exc:
        opportunities = []
        print(f"[warn] product opportunity detection failed: {exc}")
    products_affected = len(opportunities)

    # 6. Analytics records (sections 28-29) - skipped in dry-run, since
    # nothing was actually published this run for Buffer to measure.
    if dry_run:
        print("\n[info] analytics: skipped in dry run - nothing was actually published to analyze.")
    else:
        print()
        _collect_analytics_for_real(conn, config)

    succeed_job(conn, job_id)

    print()
    print(f"Topics generated: {topics_generated}")
    print(f"Topics selected: {topics_selected}")
    print(f"Scripts generated: {scripts_generated}")
    print(f"Videos rendered: {videos_rendered}")
    print(f"{schedule_label}: {posts_count}")
    print(f"Products affected: {products_affected}")
    print(f"External API writes: {'DISABLED' if dry_run else 'ENABLED'}")

    # 7. Report (section 33 step 13 / section 62's final box).
    print()
    print_report(conn, config, db_path)

    conn.close()
    return 0


def _record_usage(provider: AIProvider, usage_tracker: UsageTracker) -> None:
    usage = getattr(provider, "last_usage", None) or {}
    usage_tracker.record_call(
        prompt_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("output_tokens", 0)
    )


def _publish_for_real(
    conn: sqlite3.Connection, config: Config, content_count: int
) -> tuple[int, str | None]:
    """The live-mode "publication schedule" step: sync Buffer channels
    and actually schedule/draft READY content, same as `ghostcash
    publish`. Never raises - a missing credential, unconfigured
    channel, or Buffer error is reported and treated as "0 scheduled
    this run" rather than failing the whole `ghostcash run`, since
    content generation/rendering already succeeded and shouldn't be
    thrown away over a publishing-specific problem."""
    missing = validate_environment(["publishing"])
    if missing:
        return 0, (
            f"[info] publishing skipped - missing environment variable(s): {', '.join(missing)}."
        )

    try:
        publisher = get_publisher(config)
        media_host = get_media_host(config)
        if not isinstance(publisher, BufferPublisher):
            return 0, "[info] publishing skipped - configured publisher isn't Buffer-based."
        organization_id = resolve_organization_id(publisher, config)
        sync_channels(publisher, conn, organization_id)
        outcomes = publish_batch(publisher, media_host, conn, config, count=content_count)
    except PublishingSetupError as exc:
        return 0, f"[info] {exc}"
    except (PublisherError, MediaHostError) as exc:
        return 0, f"[warn] publishing failed: {exc}"

    succeeded = [outcome for outcome in outcomes if outcome.result is not None]
    return len(succeeded), None


def _collect_analytics_for_real(conn: sqlite3.Connection, config: Config) -> None:
    """The live-mode "analytics records" step, same as `ghostcash
    analytics`. Never raises, for the same reason as
    _publish_for_real()."""
    missing = validate_environment(["publishing"])
    if missing:
        print(f"[info] analytics skipped - missing environment variable(s): {', '.join(missing)}.")
        return

    try:
        publisher = get_publisher(config)
        if not isinstance(publisher, BufferPublisher):
            print("[info] analytics skipped - configured publisher isn't Buffer-based.")
            return
        collection = collect_analytics(publisher, conn)
    except PublisherError as exc:
        print(f"[warn] analytics collection failed: {exc}")
        return

    winner_results = evaluate_winners(conn, config)
    print(
        f"Checked {collection.posts_checked} post(s): "
        f"{collection.posts_marked_sent} newly confirmed sent, "
        f"{collection.metrics_collected} with metrics collected. "
        f"Evaluated {len(winner_results)} content item(s) for Winner Score."
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
