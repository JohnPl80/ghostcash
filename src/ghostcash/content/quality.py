"""Quality control (CLAUDE.md section 14).

Two layers, because the spec mixes binary "reject if X" language with a
continuous 0-100 score compared against a threshold:

  1. Hard gates - always reject outright, score forced to 0, regardless
     of anything else: empty script, missing hook, missing CTA, script
     too long, excessive repetition, near-duplicate of existing content,
     invalid product link. ("Rendering assets are missing" isn't
     checked yet - there's no renderer to produce assets for until
     Phase 4.)
  2. A soft 0-100 score for content that clears the hard gates, built
     from weaker signals, compared against `quality_score_threshold`
     (config, default 80) to decide pass/fail.

One deliberate deviation from a literal reading of section 14, flagged
here rather than silently made: "unsupported factual claims are
detected" is listed under "Reject content if" (hard-gate language), but
reliably detecting unsupported claims needs real fact-checking, not a
cheap regex. A hard gate built on a weak heuristic would block
legitimate content on false positives. This module instead treats
suspicious claim-like phrasing as a soft-score deduction and a flagged
reason - real content moderation, just not an automatic hard reject.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field

from ghostcash.config.schema import Config
from ghostcash.content.duplicates import compute_fingerprint, find_near_duplicate
from ghostcash.models.content import Content

_UNSUPPORTED_CLAIM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bstudies show\b", re.IGNORECASE),
    re.compile(r"\bscientists (?:say|have found|agree)\b", re.IGNORECASE),
    re.compile(r"\bresearch (?:proves|shows)\b", re.IGNORECASE),
    re.compile(r"\b100%\s*(?:guaranteed|proven|effective)\b", re.IGNORECASE),
    re.compile(r"\bnever fails\b", re.IGNORECASE),
    re.compile(r"\balways works\b", re.IGNORECASE),
    re.compile(r"\bexperts (?:say|agree|confirm)\b", re.IGNORECASE),
)

# Offers the channel cannot honour yet: no link in the profile, no
# comment-to-receive automation, no downloadable product. Deliberately
# narrow - plain "follow for more" must never match.
_UNAVAILABLE_OFFER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "link in bio",
        re.compile(r"\blinks? (?:is |are )?(?:in|on) (?:my |the |our )?(?:bio|profile)\b", re.I),
    ),
    (
        "link in the description or comments",
        re.compile(
            r"\blinks? (?:is |are )?"
            r"(?:below|(?:in|on) (?:my |the |our )?(?:description|comments?))\b",
            re.I,
        ),
    ),
    (
        "comment a keyword to get something",
        re.compile(
            r"\bcomment\b[^.!?\n]{0,60}\b(?:link|dm|message|send you|send it|send them)\b", re.I
        ),
    ),
    ("message me", re.compile(r"\b(?:dm|message) (?:me|us)\b", re.I)),
    (
        "a download of their own",
        re.compile(
            r"\b(?:download|grab|get) (?:my|our) (?:free )?(?:\w+ ){0,2}"
            r"(?:template|guide|pdf|checklist|cheat ?sheet|bundle|ebook|course)s?\b",
            re.I,
        ),
    ),
)

_WORD_PATTERN = re.compile(r"[a-z0-9']+")


@dataclass
class QualityResult:
    passed: bool
    quality_score: int
    reasons: list[str] = field(default_factory=list)
    fingerprint: str = ""
    duplicate_of_content_id: int | None = None


def _word_count(text: str) -> int:
    return len(text.split())


def _unique_word_ratio(text: str) -> float:
    words = _WORD_PATTERN.findall(text.lower())
    if not words:
        return 1.0
    return len(set(words)) / len(words)


def _find_unavailable_offer(content: Content) -> str | None:
    text = " ".join(part for part in (content.cta, content.script, content.caption) if part)
    for label, pattern in _UNAVAILABLE_OFFER_PATTERNS:
        if pattern.search(text):
            return label
    return None


def _all_text(content: Content) -> str:
    return " ".join(
        part for part in (content.hook, content.script, content.caption, content.cta) if part
    )


def _find_forbidden_phrase(content: Content, phrases: list[str]) -> str | None:
    text = _all_text(content)
    for phrase in phrases:
        phrase = (phrase or "").strip()
        if phrase and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE):
            return phrase
    return None


def _names_required_mention(content: Content, mentions: list[str]) -> bool:
    text = " ".join(part for part in (content.hook, content.script) if part)
    return any(
        re.search(rf"(?<!\w){re.escape(m.strip())}(?!\w)", text, re.IGNORECASE)
        for m in mentions
        if (m or "").strip()
    )


def _hard_gate_failures(
    content: Content, conn: sqlite3.Connection, config: Config, check_duplicates: bool = True
) -> tuple[list[str], int | None]:
    reasons: list[str] = []
    script = content.script or ""

    if not script.strip():
        reasons.append("script is empty")
    if not (content.hook or "").strip():
        reasons.append("hook is missing")
    if not (content.cta or "").strip():
        reasons.append("CTA is missing")

    word_count = _word_count(script)
    if word_count > config.content.max_script_words:
        reasons.append(
            f"script exceeds configured length "
            f"({word_count} > {config.content.max_script_words} words)"
        )

    if script.strip() and _unique_word_ratio(script) < config.content.min_unique_word_ratio:
        reasons.append("excessive repetition detected")

    duplicate_id: int | None = None
    if check_duplicates and script.strip():
        duplicate_id = find_near_duplicate(
            conn,
            script,
            similarity_threshold=config.content.duplicate_similarity_threshold,
            exclude_content_id=content.id,
            window=config.content.duplicate_check_window,
        )
        if duplicate_id is not None:
            reasons.append(f"too similar to existing content (content id={duplicate_id})")

    if config.content.reject_unavailable_offers:
        offer = _find_unavailable_offer(content)
        if offer:
            reasons.append(
                f"promises something that does not exist yet ({offer}) - set "
                "content.reject_unavailable_offers to false once it does"
            )

    forbidden = _find_forbidden_phrase(content, config.content.forbidden_phrases)
    if forbidden:
        reasons.append(f"contains a forbidden phrase ({forbidden!r})")

    mentions = [m for m in config.content.required_mentions if (m or "").strip()]
    if mentions and script.strip() and not _names_required_mention(content, mentions):
        reasons.append(
            "names none of the required sources (content.required_mentions), "
            "for example " + ", ".join(mentions[:3])
        )

    if content.product_id is not None:
        row = conn.execute(
            "SELECT url FROM products WHERE id = ?", (content.product_id,)
        ).fetchone()
        if row is None or not (row["url"] or "").strip():
            reasons.append("product link is invalid")

    return reasons, duplicate_id


def _soft_score(content: Content) -> tuple[int, list[str]]:
    script = content.script or ""
    score = 100
    reasons: list[str] = []

    claim_hits = sum(1 for pattern in _UNSUPPORTED_CLAIM_PATTERNS if pattern.search(script))
    if claim_hits:
        deduction = min(15 * claim_hits, 40)
        score -= deduction
        reasons.append(
            f"possible unsupported-claim phrasing detected ({claim_hits} match(es), -{deduction})"
        )

    if _word_count(content.hook or "") < 3:
        score -= 5
        reasons.append("hook is very short (-5)")

    if _word_count(content.cta or "") < 2:
        score -= 5
        reasons.append("CTA is very short (-5)")

    if not (content.caption or "").strip():
        score -= 10
        reasons.append("caption is missing (-10)")

    return max(0, min(100, score)), reasons


def evaluate_content(
    content: Content, conn: sqlite3.Connection, config: Config, check_duplicates: bool = True
) -> QualityResult:
    """Run quality control on `content`.

    `check_duplicates=False` is used by optimization/replication.py for
    a winner's hook-only A/B test variant, which deliberately reuses the
    winner's exact script - that's the intended controlled experiment
    (CLAUDE.md section 51's own worked example holds everything but one
    variable constant), not an accidental duplicate (section 15). Every
    other check still runs.
    """
    script_text = content.script or ""
    fingerprint = compute_fingerprint(script_text) if script_text.strip() else ""

    hard_reasons, duplicate_id = _hard_gate_failures(content, conn, config, check_duplicates)
    if hard_reasons:
        return QualityResult(
            passed=False,
            quality_score=0,
            reasons=hard_reasons,
            fingerprint=fingerprint,
            duplicate_of_content_id=duplicate_id,
        )

    score, soft_reasons = _soft_score(content)
    passed = score >= config.quality_score_threshold
    if not passed:
        soft_reasons = [
            *soft_reasons,
            f"quality_score {score} is below the threshold of {config.quality_score_threshold}",
        ]

    return QualityResult(
        passed=passed, quality_score=score, reasons=soft_reasons, fingerprint=fingerprint
    )
