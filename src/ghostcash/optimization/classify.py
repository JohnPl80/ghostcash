"""Cheap heuristic classifiers for hook/CTA "type" (CLAUDE.md section 31:
"maintain statistics by ... hook type ... CTA").

Not real NLP - keyword/pattern heuristics, the same "cheap local
mechanism first" philosophy this project already uses elsewhere
(content/duplicates.py uses difflib rather than embeddings;
content/quality.py flags unsupported claims with regex rather than an
LLM call). Good enough to group similar hooks/CTAs for statistics and
weight-learning; not a claim of precise semantic classification.
"""

from __future__ import annotations

import re

HOOK_TYPES: tuple[str, ...] = ("QUESTION", "NUMBER_LIST", "WARNING", "STATEMENT")
CTA_TYPES: tuple[str, ...] = ("FOLLOW", "COMMENT", "LINK_IN_BIO", "SAVE_SHARE", "OTHER")

_NUMBER_PATTERN = re.compile(r"\b\d+\b")
_WARNING_WORDS = re.compile(
    r"\b(stop|never|mistake|wrong|warning|danger|avoid|don't|do not|stupid)\b", re.IGNORECASE
)


def classify_hook(hook: str | None) -> str:
    """Classify a hook into one of HOOK_TYPES, checked in a fixed
    priority order so a hook matching more than one pattern (e.g. "3
    mistakes you're still making?") picks the most specific signal
    first: an explicit question mark beats a number, which beats a
    generic warning word."""
    text = (hook or "").strip()
    if not text:
        return "STATEMENT"
    if text.endswith("?"):
        return "QUESTION"
    if _NUMBER_PATTERN.search(text):
        return "NUMBER_LIST"
    if _WARNING_WORDS.search(text):
        return "WARNING"
    return "STATEMENT"


def classify_cta(cta: str | None) -> str:
    """Classify a CTA into one of CTA_TYPES by keyword."""
    text = (cta or "").strip().lower()
    if not text:
        return "OTHER"
    if "follow" in text:
        return "FOLLOW"
    if "comment" in text:
        return "COMMENT"
    if "bio" in text or "link" in text:
        return "LINK_IN_BIO"
    if "save" in text or "share" in text:
        return "SAVE_SHARE"
    return "OTHER"
