from __future__ import annotations

from ghostcash.video.subtitles import build_subtitle_cues, write_ass_subtitles


def test_build_subtitle_cues_covers_full_duration():
    cues = build_subtitle_cues("Hello there. How are you today? I am doing fine.", 10.0)
    assert len(cues) == 3
    assert cues[0][0] == 0.0
    assert cues[-1][1] == 10.0


def test_build_subtitle_cues_empty_script_returns_empty():
    assert build_subtitle_cues("", 10.0) == []


def test_build_subtitle_cues_zero_duration_returns_empty():
    assert build_subtitle_cues("Hello there.", 0.0) == []


def test_build_subtitle_cues_longer_sentences_get_more_time():
    cues = build_subtitle_cues("Hi. This is a much longer sentence than the first one.", 20.0)
    first_duration = cues[0][1] - cues[0][0]
    second_duration = cues[1][1] - cues[1][0]
    assert second_duration > first_duration


def test_write_ass_subtitles_creates_valid_file(tmp_path):
    path = tmp_path / "subs.ass"
    result = write_ass_subtitles(
        "Hello there. How are you?",
        5.0,
        path,
        width=1080,
        height=1920,
        font_family="DejaVu Sans",
        font_size=40,
    )
    content = result.read_text(encoding="utf-8")
    assert "PlayResX: 1080" in content
    assert "PlayResY: 1920" in content
    assert "Style: Default,DejaVu Sans,40" in content
    assert "Dialogue:" in content
    assert "Hello there." in content


def test_write_ass_subtitles_escapes_braces_and_backslashes(tmp_path):
    path = tmp_path / "subs.ass"
    result = write_ass_subtitles(
        "This has {curly braces} and a backslash \\ in it.",
        5.0,
        path,
        width=1080,
        height=1920,
        font_family="DejaVu Sans",
        font_size=40,
    )
    content = result.read_text(encoding="utf-8")
    assert "\\{curly braces\\}" in content
    assert "\\\\" in content


def test_write_ass_subtitles_color_conversion(tmp_path):
    path = tmp_path / "subs.ass"
    result = write_ass_subtitles(
        "Hello.",
        2.0,
        path,
        width=1080,
        height=1920,
        font_family="DejaVu Sans",
        font_size=40,
        text_color="#FF0000",
    )
    content = result.read_text(encoding="utf-8")
    # '#FF0000' (red) -> ASS '&H000000FF&' (alpha, BGR order).
    assert "&H000000FF&" in content
