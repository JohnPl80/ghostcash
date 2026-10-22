"""Removes a hook or call to action that the AI repeated inside the script.

The video narrates three parts in order: the hook, the script, the call to
action. The AI often writes its "script" as the whole voiceover - opening
line and closing line included - so the finished video said the opening
line twice at the start and the closing line twice at the end (seen on the
owner's first 50 videos, 2026-10-22).

`strip_repeated_hook_and_cta` drops those repeats from the script. It
compares by meaning of the words, not exact characters (case, punctuation
and small rewordings such as "Follow for more." versus "Follow for more
Excel tips." still count as the same line), and never empties the script.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# How alike (0-1) a sentence and the hook/CTA must be to count as a repeat.
_SIMILARITY_THRESHOLD = 0.75
# A hook or CTA can itself be a couple of sentences long.
_MAX_HOOK_SENTENCES = 3
_MAX_CTA_SENTENCES = 2
# Fewest words the script keeps; below this the cleanup is skipped.
_MIN_REMAINING_WORDS = 5

_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
# Same sentence boundary as video/text_utils.py. Kept here rather than imported:
# importing ghostcash.video from the content package is circular, because the
# video renderer uses this module.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_BOUNDARY.split(text.strip()) if part.strip()]


def _normalize(text: str) -> str:
    return " ".join(_PUNCTUATION.sub(" ", text.lower()).split())


def _similarity(first: str, second: str) -> float:
    """0-1 likeness of two lines, ignoring case and punctuation."""
    a, b = _normalize(first), _normalize(second)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = sorted((a, b), key=len)
    # One line wholly inside the other ("follow for more" inside "follow for
    # more excel tips") is a repeat, provided it is a large part of it.
    if len(shorter.split()) >= 3 and shorter in longer and len(shorter) >= 0.5 * len(longer):
        ratio = max(ratio, 0.9)
    return ratio


def _repeat_length(
    sentences: list[str], reference: str, max_sentences: int, *, from_end: bool
) -> int:
    """How many leading (or trailing) sentences repeat `reference`; 0 if none.
    The count that matches best wins, so a two-sentence hook is removed whole
    rather than leaving its second half behind."""
    best_count, best_score = 0, -1.0
    for count in range(1, min(max_sentences, len(sentences)) + 1):
        chunk = sentences[-count:] if from_end else sentences[:count]
        score = _similarity(" ".join(chunk), reference)
        if score > best_score:
            best_count, best_score = count, score
    return best_count if best_score >= _SIMILARITY_THRESHOLD else 0


def strip_repeated_hook_and_cta(script: str | None, hook: str | None, cta: str | None) -> str:
    """The script without a copy of the hook at its start or of the call to
    action at its end. Returns the script unchanged when nothing repeats, or
    when removing the repeats would leave almost nothing."""
    original = (script or "").strip()
    sentences = _split_sentences(original)
    if len(sentences) < 2:
        return original

    start, end = 0, len(sentences)
    hook_text = (hook or "").strip()
    if hook_text:
        start = _repeat_length(sentences, hook_text, _MAX_HOOK_SENTENCES, from_end=False)
    cta_text = (cta or "").strip()
    if cta_text:
        end -= _repeat_length(sentences[start:], cta_text, _MAX_CTA_SENTENCES, from_end=True)

    kept = sentences[start:end]
    if (start == 0 and end == len(sentences)) or len(" ".join(kept).split()) < _MIN_REMAINING_WORDS:
        return original
    return " ".join(kept)
