"""Subtitle generation (CLAUDE.md section 16 - "subtitles" is a required
renderer input/capability).

Generates ASS (Advanced SubStation Alpha), not plain SRT, and this is a
deliberate, empirically-verified choice, not a style preference: ffmpeg's
`subtitles` filter renders bare SRT through an auto-generated ASS script
with an assumed internal resolution. When that internal resolution
doesn't match the real video frame, `force_style='FontSize=...'`
overrides get scaled by the mismatch and can render many times too
large (confirmed while building this - a FontSize=40 style rendered at
roughly 6x the intended size and word-wrapped one word per line on a
1080x1920 frame). Writing the `.ass` file directly with explicit
PlayResX/PlayResY equal to the real output resolution avoids the
mismatch entirely, since the style is authored in the same coordinate
space video is rendered in.

There's no free/local forced-alignment available without a much heavier
dependency (e.g. a speech-to-text model), so cue timing is approximated:
each sentence gets a slice of the audio proportional to its character
length. Not word-accurate, but deterministic, free, instant, and close
enough for short-form captions with fairly even speech pace - the same
trade made in content/duplicates.py for duplicate detection.
"""

from __future__ import annotations

from pathlib import Path

from ghostcash.video.text_utils import split_sentences

MIN_CUE_SECONDS = 0.8


def _format_ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, remainder = divmod(remainder, 60)
    secs, fraction = divmod(remainder, 1)
    centiseconds = round(fraction * 100)
    return f"{int(hours):d}:{int(minutes):02d}:{int(secs):02d}.{centiseconds:02d}"


def _escape_ass_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    return text


def _hex_to_ass_color(hex_color: str) -> str:
    """'#RRGGBB' -> ASS's '&H00BBGGRR&' (alpha, then blue-green-red)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H00{b}{g}{r}".upper() + "&"


def build_subtitle_cues(
    script: str, total_duration_seconds: float
) -> list[tuple[float, float, str]]:
    """Return (start_seconds, end_seconds, text) cues covering the full duration."""
    sentences = split_sentences(script)
    if not sentences or total_duration_seconds <= 0:
        return []

    weights = [max(len(s), 1) for s in sentences]
    total_weight = sum(weights)

    cues: list[tuple[float, float, str]] = []
    cursor = 0.0
    for sentence, weight in zip(sentences, weights, strict=True):
        share = (weight / total_weight) * total_duration_seconds
        start = cursor
        end = min(total_duration_seconds, cursor + max(share, MIN_CUE_SECONDS))
        cues.append((start, end, sentence))
        cursor = end

    # Make sure the last cue reaches the true end even after MIN_CUE_SECONDS
    # flooring nudged things earlier than the audio's actual length.
    if cues and cues[-1][1] < total_duration_seconds:
        start, _, text = cues[-1]
        cues[-1] = (start, total_duration_seconds, text)

    return cues


def write_ass_subtitles(
    script: str,
    total_duration_seconds: float,
    output_path: str | Path,
    *,
    width: int,
    height: int,
    font_family: str,
    font_size: int,
    text_color: str = "#FFFFFF",
    outline_color: str = "#000000",
    margin_v: int = 120,
) -> Path:
    cues = build_subtitle_cues(script, total_duration_seconds)

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, "
        "Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
        "MarginV, Encoding",
        (
            f"Style: Default,{font_family},{font_size},{_hex_to_ass_color(text_color)},"
            f"{_hex_to_ass_color(outline_color)},&H00000000,-1,0,1,3,0,2,60,60,{margin_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    for start, end, text in cues:
        lines.append(
            f"Dialogue: 0,{_format_ass_timestamp(start)},{_format_ass_timestamp(end)},"
            f"Default,,0,0,0,,{_escape_ass_text(text)}"
        )

    path = Path(output_path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
