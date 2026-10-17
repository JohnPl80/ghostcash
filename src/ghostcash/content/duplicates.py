"""Duplicate detection (CLAUDE.md section 15).

"Semantic-ish duplicate detection using a cheap local mechanism" is
implemented here as:

  1. An exact-match fingerprint: SHA-256 of the normalized script text,
     stored on `content.fingerprint`, for instant identical-text checks.
  2. Lexical similarity (difflib.SequenceMatcher) against a window of
     recent scripts, for near-duplicates that aren't byte-identical.

Be clear about what this does and doesn't catch: it's textual/lexical
similarity, not true semantic similarity. Two scripts saying the same
thing in very different words will NOT be flagged. That's a deliberate
trade-off for "cheap and local" (no embeddings, no external API calls,
no vector database) - CLAUDE.md sections 4 and 67 prefer boring and
free over sophisticated. If near-duplicates start slipping through in
practice, upgrading to embedding-based similarity is the natural next
step, at the cost of an API call (and $) per piece of content.
"""

from __future__ import annotations

import difflib
import hashlib
import re
import sqlite3

_NON_ALNUM = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    text = text.lower()
    text = _NON_ALNUM.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text


def compute_fingerprint(script: str) -> str:
    return hashlib.sha256(normalize_text(script).encode("utf-8")).hexdigest()


def find_near_duplicate(
    conn: sqlite3.Connection,
    script: str,
    similarity_threshold: float,
    exclude_content_id: int | None = None,
    window: int = 200,
) -> int | None:
    """Return the id of the most similar recent content row that meets
    or exceeds `similarity_threshold`, or None if no such row exists.

    Compares against the most recent `window` content rows with a
    non-empty script (recent winners are the ones most worth
    protecting against accidental re-generation).
    """
    normalized_new = normalize_text(script)
    if not normalized_new:
        return None

    rows = conn.execute(
        "SELECT id, script FROM content "
        "WHERE script IS NOT NULL AND script != '' AND id != COALESCE(?, -1) "
        "ORDER BY id DESC LIMIT ?",
        (exclude_content_id, window),
    ).fetchall()

    for row in rows:
        normalized_existing = normalize_text(row["script"])
        if not normalized_existing:
            continue
        ratio = difflib.SequenceMatcher(None, normalized_new, normalized_existing).ratio()
        if ratio >= similarity_threshold:
            return int(row["id"])

    return None
