"""Winner replication (CLAUDE.md section 30).

For every WINNER content item not yet replicated, ask the AI for 5
alternative hooks, 5 alternative scripts, 3 alternative CTAs, and 3
related topics - stored as `content_variants` rows (the same table
content/generation.py already uses for a winner's own alternative
hook; see its docstring for why that table, not a new column/table).

One of the five alternative hooks is also promoted into a new, real
`content` row that is otherwise IDENTICAL to the winner (same script,
caption, CTA, content_type, category, topic) and paired with it in a
new `experiments` row (CLAUDE.md section 51) - deliberately varying
exactly one variable, the same shape as section 51's own worked
example ("Control: ... Variant: ..." - same idea, different hook
phrasing only). That one deliberate hook-only duplicate is the control
arm of an actual controlled experiment, not a lapse into section 30's
"do NOT simply duplicate the original" - that instruction is about the
5/5/3/3 pool as a whole (real alternatives, not five rewordings of one
idea), not about the A/B test variant's relationship to its own control.

Because the variant's script is an intentional, byte-identical copy of
the winner's, it is run through quality control with
`check_duplicates=False` (see content/quality.py) - the ordinary
near-duplicate hard gate exists to catch *accidental* repetition, and
would otherwise reject the control's own partner on sight.

Capped at `max_winners_replicated_per_run` winners per call (CLAUDE.md
section 46 cost control - each replicated winner costs one AI call);
any remainder is picked up on a later `ghostcash optimize` run, since
`content.winner_replicated_at` is only set once a winner is actually
replicated (CLAUDE.md section 56/57 - idempotent, safe to re-run).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.config.schema import Config
from ghostcash.content.quality import evaluate_content
from ghostcash.models.content import Content
from ghostcash.optimization.classify import classify_hook

_VARIANT_TYPE_FOR_FIELD: dict[str, str] = {
    "alternative_hooks": "HOOK",
    "alternative_scripts": "SCRIPT",
    "alternative_ctas": "CTA",
    "related_topics": "TOPIC",
}


class ReplicationError(Exception):
    """Raised when winner replication cannot produce any usable variants."""


@dataclass
class ReplicationResult:
    content_id: int
    variant_content_id: int | None
    experiment_key: str | None
    hooks_generated: int
    scripts_generated: int
    ctas_generated: int
    topics_generated: int


def _variant_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "alternative_hooks": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exactly 5 alternative opening hooks for this same winning video - "
                    "each a genuinely different angle from the original and from each other."
                ),
            },
            "alternative_scripts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exactly 5 alternative full scripts exploring the same core idea from "
                    "genuinely different angles - not just reworded, each its own take."
                ),
            },
            "alternative_ctas": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Exactly 3 alternative calls to action.",
            },
            "related_topics": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Exactly 3 related but DIFFERENT topic ideas this winning content "
                    "suggests exploring next - genuinely adjacent ideas, not variations "
                    "of this one."
                ),
            },
        },
        "required": list(_VARIANT_TYPE_FOR_FIELD),
    }


def _build_prompt(winner: Content, cta_guidance: str = "") -> str:
    guidance = (cta_guidance or "").strip()
    cta_rule = f"For the calls to action: {guidance}\n" if guidance else ""
    return (
        "This short-form video performed exceptionally well:\n"
        f"Category: {winner.category}\n"
        f'Hook: "{winner.hook}"\n'
        f'Script: "{winner.script}"\n'
        f'CTA: "{winner.cta}"\n\n'
        "It's a proven winner - generate material to test whether it can be improved "
        "further, and to find related ideas worth exploring next. Provide:\n"
        "- 5 alternative hooks for a NEW video with this EXACT same script and CTA "
        "(different angles from each other and from the original - this is for a "
        "controlled hook A/B test, so every alternative must still make sense with "
        "the existing script)\n"
        "- 5 alternative full scripts exploring the same core idea from genuinely "
        "different angles (not reworded copies of the original)\n"
        "- 3 alternative calls to action\n"
        "- 3 related but different topic ideas this success suggests, for future videos\n"
        "Avoid generic motivational filler, unsupported claims, fabricated statistics, "
        "fabricated quotations, or plagiarized ideas.\n"
        f"{cta_rule}"
    )


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _insert_variants(
    conn: sqlite3.Connection, parent_content_id: int, variant_type: str, texts: list[str]
) -> list[int]:
    ids: list[int] = []
    for text in texts:
        cursor = conn.execute(
            "INSERT INTO content_variants (parent_content_id, variant_type, variant_text, status) "
            "VALUES (?, ?, ?, 'PROPOSED')",
            (parent_content_id, variant_type, text),
        )
        assert cursor.lastrowid is not None  # AUTOINCREMENT guarantees this
        ids.append(cursor.lastrowid)
    conn.commit()
    return ids


def _pick_experiment_hook_index(original_hook: str | None, alternative_hooks: list[str]) -> int:
    """Prefer an alternative whose classified hook type (see
    optimization/classify.py) differs from the original's - maximizes
    what the experiment can actually teach us. Falls back to the first
    alternative if every one happens to classify the same way."""
    original_type = classify_hook(original_hook)
    for index, candidate in enumerate(alternative_hooks):
        if classify_hook(candidate) != original_type:
            return index
    return 0


def _insert_variant_content(conn: sqlite3.Connection, content: Content) -> int:
    """Insert a hook-variant content row directly (not via
    content/generation.py's `_insert_content`, which expects a raw
    `GeneratedDraft.visual_sequence` list to serialize - here
    `visual_sequence_json` is already a serialized string copied
    straight from the winner, since the variant reuses its visuals)."""
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
            content.visual_sequence_json,
            content.suggested_product_description,
        ),
    )
    conn.commit()
    assert cursor.lastrowid is not None  # AUTOINCREMENT guarantees this
    content.id = cursor.lastrowid
    return content.id


def _create_experiment(
    conn: sqlite3.Connection,
    winner: Content,
    variant_content_id: int,
    variant_hook: str,
    content_variant_id: int | None,
) -> str:
    key = f"winner-hook-replication:{winner.id}"
    conn.execute(
        """
        INSERT INTO experiments (
            experiment_key, hypothesis, variable, control_description,
            variant_description, control_content_id, variant_content_id,
            content_variant_id, metric_name, status
        ) VALUES (?, ?, 'hook', ?, ?, ?, ?, ?, 'engagement_rate', 'PENDING')
        ON CONFLICT(experiment_key) DO NOTHING
        """,
        (
            key,
            "A different hook style may outperform this proven winner's original hook.",
            winner.hook or "",
            variant_hook,
            winner.id,
            variant_content_id,
            content_variant_id,
        ),
    )
    conn.commit()
    return key


def _load_winner(conn: sqlite3.Connection, content_id: int) -> Content | None:
    row = conn.execute("SELECT * FROM content WHERE id = ?", (content_id,)).fetchone()
    return Content.from_row(row) if row is not None else None


def _replicate_one(
    ai_provider: AIProvider,
    conn: sqlite3.Connection,
    config: Config,
    winner: Content,
    usage_tracker: UsageTracker | None,
) -> ReplicationResult:
    assert winner.id is not None
    prompt = _build_prompt(winner, config.content.cta_guidance)
    try:
        result = ai_provider.structured_generate(prompt, _variant_schema())
    except AIProviderError as exc:
        raise ReplicationError(f"AI provider call failed for winner #{winner.id}: {exc}") from exc

    if usage_tracker is not None:
        usage = getattr(ai_provider, "last_usage", None) or {}
        usage_tracker.record_call(
            prompt_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("output_tokens", 0)
        )

    raw = result if isinstance(result, dict) else {}
    hooks = _string_list(raw.get("alternative_hooks"))
    scripts = _string_list(raw.get("alternative_scripts"))
    ctas = _string_list(raw.get("alternative_ctas"))
    related_topics = _string_list(raw.get("related_topics"))

    if not any((hooks, scripts, ctas, related_topics)):
        raise ReplicationError(f"AI response for winner #{winner.id} contained no usable variants")

    hook_ids = _insert_variants(conn, winner.id, "HOOK", hooks)
    script_ids = _insert_variants(conn, winner.id, "SCRIPT", scripts)
    cta_ids = _insert_variants(conn, winner.id, "CTA", ctas)
    topic_ids = _insert_variants(conn, winner.id, "TOPIC", related_topics)

    variant_content_id: int | None = None
    experiment_key: str | None = None
    if hooks:
        index = _pick_experiment_hook_index(winner.hook, hooks)
        chosen_hook = hooks[index]
        chosen_variant_id = hook_ids[index] if index < len(hook_ids) else None

        variant_content = Content(
            topic=winner.topic,
            category=winner.category,
            topic_id=winner.topic_id,
            title=winner.title,
            hook=chosen_hook,
            script=winner.script,
            caption=winner.caption,
            cta=winner.cta,
            content_type=winner.content_type,
            status="DRAFT",
            visual_sequence_json=winner.visual_sequence_json,
            suggested_product_description=winner.suggested_product_description,
        )
        quality = evaluate_content(variant_content, conn, config, check_duplicates=False)
        variant_content.quality_score = quality.quality_score
        variant_content.fingerprint = quality.fingerprint
        variant_content.status = "APPROVED" if quality.passed else "REJECTED"
        variant_content_id = _insert_variant_content(conn, variant_content)

        if chosen_variant_id is not None:
            conn.execute(
                "UPDATE content_variants SET status = 'TESTING' WHERE id = ?",
                (chosen_variant_id,),
            )
            conn.commit()

        experiment_key = _create_experiment(
            conn, winner, variant_content_id, chosen_hook, chosen_variant_id
        )

    conn.execute(
        "UPDATE content SET winner_replicated_at = ? WHERE id = ?",
        (datetime.now(UTC).isoformat(), winner.id),
    )
    conn.commit()

    return ReplicationResult(
        content_id=winner.id,
        variant_content_id=variant_content_id,
        experiment_key=experiment_key,
        hooks_generated=len(hook_ids),
        scripts_generated=len(script_ids),
        ctas_generated=len(cta_ids),
        topics_generated=len(topic_ids),
    )


def replicate_winners(
    ai_provider: AIProvider,
    conn: sqlite3.Connection,
    config: Config,
    usage_tracker: UsageTracker | None = None,
) -> list[ReplicationResult]:
    """Replicate every WINNER content item not yet replicated, up to
    `config.optimization.max_winners_replicated_per_run`, oldest first.

    Returns a ReplicationResult per winner actually replicated (empty
    list if there was nothing due). Raises ReplicationError only if
    winners were due and the AI provider failed on every single one of
    them - matching content/generation.py:generate_scripts()'s
    "best-effort across items, raise only on total failure" shape.
    """
    limit = config.optimization.max_winners_replicated_per_run
    if limit < 1:
        return []

    rows = conn.execute(
        "SELECT id FROM content WHERE status = 'WINNER' AND winner_replicated_at IS NULL "
        "ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return []

    results: list[ReplicationResult] = []
    failures = 0
    for row in rows:
        winner = _load_winner(conn, row["id"])
        if winner is None:
            continue
        try:
            results.append(_replicate_one(ai_provider, conn, config, winner, usage_tracker))
        except ReplicationError:
            failures += 1
            continue

    if not results and failures == len(rows):
        raise ReplicationError("AI provider failed on every winner due for replication.")

    return results
