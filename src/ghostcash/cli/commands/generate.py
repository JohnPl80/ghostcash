"""`ghostcash generate` (CLAUDE.md sections 11, 13-15).

Runs topic generation, then content generation + quality control for
the best available candidate topics - matching the pipeline shape in
section 62's Definition of Done (20 topics -> 5 selected -> 5 scripts).

The two steps are tracked as separate jobs and fail independently: a
topic-generation hiccup (e.g. a rate limit) shouldn't stop content
generation from using whatever candidate topics already exist from
previous runs, and vice versa. The command only exits non-zero if BOTH
steps failed outright.
"""

from __future__ import annotations

import argparse
import traceback

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.ai.factory import get_ai_provider
from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.config.schema import Config
from ghostcash.content.generation import ContentGenerationError, generate_scripts
from ghostcash.content.topics import TopicGenerationError, generate_topics
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    missing = validate_environment(["ai"])
    if missing:
        print(f"[error] missing required environment variable(s): {', '.join(missing)}")
        print("        set them in .env, then re-run `ghostcash generate`.")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    provider = get_ai_provider(config.ai)
    usage_tracker = UsageTracker(conn)

    topics_ok = _run_topic_generation(provider, conn, config, args, usage_tracker)
    content_ok = _run_content_generation(provider, conn, config, args, usage_tracker)

    conn.close()
    return 0 if (topics_ok or content_ok) else 1


def _run_topic_generation(
    provider: AIProvider,
    conn,
    config: Config,
    args: argparse.Namespace,
    usage_tracker: UsageTracker,
) -> bool:
    job_id = start_job(conn, "generate_topics")
    try:
        topics = generate_topics(provider, conn, config, count=args.count)
    except (AIProviderError, TopicGenerationError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        print(f"[error] topic generation failed: {exc}")
        return False

    usage = getattr(provider, "last_usage", None) or {}
    usage_tracker.record_call(
        prompt_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("output_tokens", 0)
    )
    succeed_job(conn, job_id)

    print(f"Generated {len(topics)} candidate topic(s).")
    top = sorted(topics, key=lambda t: t.overall_score or 0.0, reverse=True)[:5]
    if top:
        print("Top by opportunity score:")
        for topic in top:
            score = f"{topic.overall_score:.2f}" if topic.overall_score is not None else "n/a"
            print(f"  [{score}] ({topic.category}) {topic.topic}")
    return True


def _run_content_generation(
    provider: AIProvider,
    conn,
    config: Config,
    args: argparse.Namespace,
    usage_tracker: UsageTracker,
) -> bool:
    job_id = start_job(conn, "generate_content")
    try:
        content_items = generate_scripts(
            provider, conn, config, count=args.content_count, usage_tracker=usage_tracker
        )
    except (AIProviderError, ContentGenerationError) as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        print(f"[error] content generation failed: {exc}")
        return False

    succeed_job(conn, job_id)

    if not content_items:
        print("\nNo candidate topics were available for content generation.")
        return True

    approved = [c for c in content_items if c.status == "APPROVED"]
    rejected = [c for c in content_items if c.status == "REJECTED"]
    print(
        f"\nGenerated {len(content_items)} script(s): "
        f"{len(approved)} approved, {len(rejected)} rejected by quality control."
    )
    for content in content_items:
        score = f"{content.quality_score:.0f}" if content.quality_score is not None else "n/a"
        print(f"  [{content.status:8s} score={score}] {content.title}")
    return True
