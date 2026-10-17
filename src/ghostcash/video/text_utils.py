"""Cheap, deterministic text utilities shared by scene derivation and
subtitle generation.

No NLP library - a regex sentence splitter and a simple even-chunking
function are plenty for turning a script into subtitle cues or template
scene text, consistent with the project's "cheap and local first"
approach (see also content/duplicates.py).

wrap_text_to_width() is the one exception that reaches for a real
dependency (Pillow): ffmpeg's `drawtext` filter does not auto-wrap long
lines - text that's wider than the frame just gets clipped off both
edges. Wrapping needs to know the actual rendered width of the text in
a specific font/size, and a template's font is configurable (section
17), so a hardcoded "average character width" guess would silently be
wrong for a different font. Pillow's font metrics give an exact answer
instead.
"""

from __future__ import annotations

import re

from PIL import ImageFont

_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split text into sentences on '.', '!', or '?' followed by whitespace.

    Doesn't try to be clever about abbreviations ("Mr.", "e.g.") - for
    short-form video scripts (one idea, plain sentences) that's a
    reasonable trade rather than pulling in a real sentence tokenizer.
    """
    text = text.strip()
    if not text:
        return []
    sentences = [s.strip() for s in _SENTENCE_BOUNDARY.split(text)]
    return [s for s in sentences if s]


def chunk_evenly(items: list[str], count: int) -> list[list[str]]:
    """Split `items` into `count` groups of roughly equal size, in order.

    If there are fewer items than groups, some groups will be empty
    (callers should filter/handle that). Always returns exactly `count`
    groups.
    """
    if count <= 0:
        return []
    if not items:
        return [[] for _ in range(count)]

    groups: list[list[str]] = [[] for _ in range(count)]
    base_size, remainder = divmod(len(items), count)
    index = 0
    for group_index in range(count):
        size = base_size + (1 if group_index < remainder else 0)
        groups[group_index] = items[index : index + size]
        index += size
    return groups


def wrap_text_to_width(text: str, font_path: str, font_size: int, max_width_px: int) -> str:
    """Greedy word-wrap `text` so no line exceeds `max_width_px` when
    rendered in the given font/size, using Pillow's exact font metrics.

    Returns the text with '\\n' inserted between lines, ready to hand
    to ffmpeg's drawtext (which honors literal newlines but never wraps
    on its own). A single word wider than max_width_px is kept on its
    own line rather than split mid-word.
    """
    text = " ".join(text.split())  # normalize whitespace
    if not text:
        return text

    try:
        font = ImageFont.truetype(font_path, font_size)
    except OSError:
        # Font file not found/unreadable - fall back to returning the
        # text unwrapped rather than crashing the whole render; the
        # caller's ffmpeg invocation will surface a clearer error if
        # the font path is genuinely broken.
        return text

    words = text.split(" ")
    lines: list[str] = []
    current_line: list[str] = []

    for word in words:
        candidate = " ".join([*current_line, word])
        width = font.getlength(candidate)
        if width <= max_width_px or not current_line:
            current_line.append(word)
        else:
            lines.append(" ".join(current_line))
            current_line = [word]

    if current_line:
        lines.append(" ".join(current_line))

    return "\n".join(lines)
