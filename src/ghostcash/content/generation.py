"""Content generation (CLAUDE.md section 13), orchestrated with quality
control (section 14) and duplicate detection (section 15).

For each selected topic, this asks the AI for a full short-form video
draft (hook, script, caption, CTA, a visual sequence, an optional
product suggestion, and one alternative hook), runs it through quality
control, and persists the result - APPROVED if it clears the bar,
REJECTED if not, matching the section 7 status lifecycle:
DRAFT -> (quality control) -> APPROVED | REJECTED.

The alternative hook is stored as a content_variants row (variant_type
HOOK, status PROPOSED) rather than a new column - that table already
exists for exactly this shape of data and doubles as the seed for
winner-replication variants later (section 30).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.config.schema import Config
from ghostcash.content.quality import evaluate_content
from ghostcash.content.script_cleanup import strip_repeated_hook_and_cta
from ghostcash.models.content import Content
from ghostcash.models.topic import Topic
from ghostcash.optimization.weights import get_hook_type_weights, get_template_weights, top_value

# CLAUDE.md section 17's three video templates - content generation
# tags each script with the one it best fits, for the renderer (Phase 4)
# to pick a matching layout.
CONTENT_TYPES: tuple[str, ...] = ("INFORMATION", "LIST", "PROBLEM_SOLUTION")


class ContentGenerationError(Exception):
    """Raised when content generation cannot produce any usable content."""


@dataclass
class GeneratedDraft:
    """One AI response, parsed but not yet persisted."""

    hook: str
    script: str
    caption: str
    cta: str
    alternative_hook: str
    content_type: str
    visual_sequence: list[str]
    suggested_product_description: str | None


def _content_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "hook": {
                "type": "string",
                "description": (
                    "Opening line for the first 2 seconds. It is spoken first, on its own, "
                    "before the script."
                ),
            },
            "script": {
                "type": "string",
                "description": (
                    "The spoken BODY of the video (the 2-3 points). One idea only. Do NOT "
                    "repeat the hook at its start or the call to action at its end: the "
                    "hook is narrated before the script and the call to action after it, "
                    "so anything repeated would be said twice."
                ),
            },
            "caption": {
                "type": "string",
                "description": "Social caption to accompany the post.",
            },
            "cta": {
                "type": "string",
                "description": (
                    "A single, specific call to action, spoken last, after the script. "
                    "Do not also put it in the script."
                ),
            },
            "alternative_hook": {
                "type": "string",
                "description": "A different opening line, a genuinely different angle from `hook`.",
            },
            "content_type": {
                "type": "string",
                "enum": list(CONTENT_TYPES),
                "description": (
                    "INFORMATION = hook then 2-3 points then CTA. "
                    "LIST = '3 things...' style countdown. "
                    "PROBLEM_SOLUTION = problem, why it happens, solution, CTA."
                ),
            },
            "visual_sequence": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Ordered short descriptions of what's on screen per beat.",
            },
            "suggested_product_description": {
                "type": ["string", "null"],
                "description": (
                    "A short description of a digital product this content could plausibly "
                    "sell, or null if nothing fits."
                ),
            },
        },
        "required": [
            "hook",
            "script",
            "caption",
            "cta",
            "alternative_hook",
            "content_type",
            "visual_sequence",
            "suggested_product_description",
        ],
    }


def _performance_hint(hook_type: str | None, template: str | None) -> str:
    """A soft, non-binding nudge toward the hook style/template that has
    performed best recently (CLAUDE.md sections 31, 50: "update hook
    weights" / "update template weights" -> "use these statistics to
    improve future content selection"). Empty string if nothing has
    been learned yet - see optimization/weights.py:top_value(), which
    returns None until a dimension has enough measured data."""
    bits = []
    if hook_type:
        bits.append(f"a {hook_type.replace('_', ' ').lower()}-style hook")
    if template:
        bits.append(f"the {template.replace('_', ' ').lower()} format")
    if not bits:
        return ""
    return (
        " If it fits the topic naturally, lean toward " + " and ".join(bits) + " - these "
        "have performed best recently. Don't force either if a different approach suits "
        "this topic better."
    )


def _cta_instruction(cta_guidance: str) -> str:
    guidance = (cta_guidance or "").strip()
    return f" Call to action: {guidance}" if guidance else ""


def _build_prompt(
    topic: Topic,
    max_words: int,
    hook_type_hint: str | None,
    template_hint: str | None,
    cta_guidance: str = "",
    brand_name: str = "",
    brand_description: str = "",
    writing_guidance: str = "",
) -> str:
    audience = topic.target_audience or "a general audience"
    name = (brand_name or "").strip()
    description = " ".join((brand_description or "").split())
    channel = ""
    if name:
        about = f" ({description})." if description else "."
        channel = f' It is for the channel "{name}"{about}'
    extra = " ".join((writing_guidance or "").split())
    extra = f" Channel rules: {extra}" if extra else ""
    hook_hint = (
        f' Consider this hook as inspiration if it fits: "{topic.hook}".' if topic.hook else ""
    )
    return (
        f'Write a short-form faceless video script for this topic: "{topic.topic}" '
        f"(category: {topic.category}). Target audience: {audience}.{channel} "
        "The video is voiceover plus on-screen text only, no on-camera talent, "
        f"15-35 seconds long (roughly {max_words} words or fewer in the script). "
        "Focus on exactly ONE specific idea. "
        "The hook, the script and the call to action are narrated one after another, "
        "so the script must contain only the body: never repeat the hook at its start "
        "or the call to action at its end. "
        "Avoid generic motivational filler, obviously AI-sounding phrasing, repetitive "
        "wording, unsupported claims, fabricated statistics or quotations, and "
        f"copied or plagiarized content.{hook_hint}"
        f"{_performance_hint(hook_type_hint, template_hint)}"
        f"{extra}"
        f"{_cta_instruction(cta_guidance)}"
    )


def generate_draft_for_topic(
    ai_provider: AIProvider, topic: Topic, config: Config, conn: sqlite3.Connection
) -> GeneratedDraft:
    """Make one AI call and parse it into a GeneratedDraft. Reads (but
    does not write) the learned hook-type/template weights from `conn`
    to build a soft prompt hint - otherwise no DB access."""
    hook_type_hint = top_value(get_hook_type_weights(conn))
    template_hint = top_value(get_template_weights(conn))
    prompt = _build_prompt(
        topic,
        config.content.max_script_words,
        hook_type_hint,
        template_hint,
        config.content.cta_guidance,
        config.brand.name,
        config.brand.description,
        config.content.writing_guidance,
    )
    try:
        result = ai_provider.structured_generate(prompt, _content_schema())
    except AIProviderError as exc:
        raise ContentGenerationError(f"AI provider call failed: {exc}") from exc

    try:
        return GeneratedDraft(
            hook=str(result["hook"]),
            script=str(result["script"]),
            caption=str(result["caption"]),
            cta=str(result["cta"]),
            alternative_hook=str(result["alternative_hook"]),
            content_type=str(result["content_type"]),
            visual_sequence=[str(item) for item in (result.get("visual_sequence") or [])],
            suggested_product_description=result.get("suggested_product_description") or None,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ContentGenerationError(f"AI response missing/invalid fields: {exc}") from exc


def select_candidate_topics(conn: sqlite3.Connection, limit: int) -> list[Topic]:
    """Best not-yet-used topics, highest opportunity score first.

    Only CANDIDATE topics are selected - a topic whose content
    generation previously failed simply stays CANDIDATE (see
    _mark_topic_used), so it's automatically retried next run without
    any extra bookkeeping.
    """
    rows = conn.execute(
        "SELECT * FROM topics WHERE status = 'CANDIDATE' "
        "ORDER BY overall_score DESC, id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Topic.from_row(row) for row in rows]


def _insert_content(conn: sqlite3.Connection, content: Content, draft: GeneratedDraft) -> None:
    cursor = conn.execute(
        """
        INSERT INTO content (
            topic_id, topic, category, title, hook, script, caption, cta,
            content_type, status, quality_score, fingerprint,
            visual_sequence_json, suggested_product_description
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content.topic_id,
            content.topic,
            content.category,
            content.title,
            content.hook,
            content.script,
            content.caption,
            content.cta,
            content.content_type,
            content.status,
            content.quality_score,
            content.fingerprint,
            json.dumps(draft.visual_sequence),
            draft.suggested_product_description,
        ),
    )
    conn.commit()
    content.id = cursor.lastrowid


def _insert_alternative_hook(
    conn: sqlite3.Connection, content_id: int, alternative_hook: str
) -> None:
    if not alternative_hook.strip():
        return
    conn.execute(
        "INSERT INTO content_variants (parent_content_id, variant_type, variant_text, status) "
        "VALUES (?, 'HOOK', ?, 'PROPOSED')",
        (content_id, alternative_hook),
    )
    conn.commit()


def _mark_topic_used(conn: sqlite3.Connection, topic_id: int | None) -> None:
    if topic_id is None:
        return
    conn.execute("UPDATE topics SET status = 'USED' WHERE id = ?", (topic_id,))
    conn.commit()


def generate_scripts(
    ai_provider: AIProvider,
    conn: sqlite3.Connection,
    config: Config,
    count: int | None = None,
    usage_tracker: UsageTracker | None = None,
) -> list[Content]:
    """Generate, quality-check, and persist content for the best
    available candidate topics.

    Returns every Content row created (APPROVED or REJECTED - both are
    "results", not failures). Returns an empty list if there were no
    candidate topics to work with (not an error). Raises
    ContentGenerationError only if topics existed but the AI failed on
    every single one of them.
    """
    requested_count = count if count is not None else config.content.content_per_run
    if requested_count < 1:
        return []

    topics = select_candidate_topics(conn, requested_count)
    if not topics:
        return []

    if config.ai.max_requests_per_run < 1:
        raise ContentGenerationError(
            "AI_MAX_REQUESTS_PER_RUN is set to 0 - refusing to make any AI calls."
        )

    results: list[Content] = []
    failures = 0

    for topic in topics:
        try:
            draft = generate_draft_for_topic(ai_provider, topic, config, conn)
        except ContentGenerationError:
            failures += 1
            continue

        if usage_tracker is not None:
            usage = getattr(ai_provider, "last_usage", None) or {}
            usage_tracker.record_call(
                prompt_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
            )

        content = Content(
            topic=topic.topic,
            category=topic.category,
            topic_id=topic.id,
            title=topic.topic,
            hook=draft.hook,
            # The AI often writes the hook and call to action into the script
            # as well; the video narrates them separately, so drop the copies.
            script=strip_repeated_hook_and_cta(draft.script, draft.hook, draft.cta),
            caption=draft.caption,
            cta=draft.cta,
            content_type=draft.content_type,
            status="DRAFT",
        )

        quality = evaluate_content(content, conn, config)
        content.quality_score = quality.quality_score
        content.fingerprint = quality.fingerprint
        content.status = "APPROVED" if quality.passed else "REJECTED"

        _insert_content(conn, content, draft)
        assert content.id is not None  # AUTOINCREMENT guarantees this
        _insert_alternative_hook(conn, content.id, draft.alternative_hook)
        _mark_topic_used(conn, topic.id)

        results.append(content)

    if not results and failures:
        raise ContentGenerationError(
            f"AI provider failed on all {failures} selected topic(s); no content was generated."
        )

    return results
