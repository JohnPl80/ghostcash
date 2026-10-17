from __future__ import annotations

from ghostcash.video.text_utils import chunk_evenly, split_sentences, wrap_text_to_width

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def test_split_sentences_basic():
    assert split_sentences("Hello world. How are you? I am fine!") == [
        "Hello world.",
        "How are you?",
        "I am fine!",
    ]


def test_split_sentences_empty_string():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_sentences_single_sentence_no_terminator():
    assert split_sentences("Just one clause with no ending punctuation") == [
        "Just one clause with no ending punctuation"
    ]


def test_chunk_evenly_distributes_remainder_to_early_groups():
    groups = chunk_evenly(["a", "b", "c", "d", "e"], 2)
    assert groups == [["a", "b", "c"], ["d", "e"]]


def test_chunk_evenly_exact_division():
    groups = chunk_evenly(["a", "b", "c", "d"], 2)
    assert groups == [["a", "b"], ["c", "d"]]


def test_chunk_evenly_more_groups_than_items():
    groups = chunk_evenly(["a"], 3)
    assert len(groups) == 3
    assert groups[0] == ["a"]
    assert groups[1] == []
    assert groups[2] == []


def test_chunk_evenly_empty_items():
    groups = chunk_evenly([], 3)
    assert groups == [[], [], []]


def test_chunk_evenly_zero_count():
    assert chunk_evenly(["a", "b"], 0) == []


def test_wrap_text_to_width_short_text_stays_one_line():
    wrapped = wrap_text_to_width("Hello world", FONT_PATH, 64, max_width_px=2000)
    assert "\n" not in wrapped


def test_wrap_text_to_width_long_text_wraps_into_multiple_lines():
    text = "You are wasting hours in Excel every single week and do not even realize it"
    wrapped = wrap_text_to_width(text, FONT_PATH, 64, max_width_px=900)
    lines = wrapped.split("\n")
    assert len(lines) > 1
    # No information lost - every word should still be present, in order.
    assert " ".join(lines).split() == text.split()


def test_wrap_text_to_width_normalizes_internal_whitespace():
    wrapped = wrap_text_to_width("Hello    world\n\nagain", FONT_PATH, 40, max_width_px=2000)
    assert wrapped == "Hello world again"


def test_wrap_text_to_width_single_long_word_not_split():
    wrapped = wrap_text_to_width(
        "Supercalifragilisticexpialidocious", FONT_PATH, 64, max_width_px=10
    )
    assert wrapped == "Supercalifragilisticexpialidocious"


def test_wrap_text_to_width_missing_font_falls_back_to_unwrapped():
    wrapped = wrap_text_to_width("Hello world", "/no/such/font.ttf", 64, max_width_px=10)
    assert wrapped == "Hello world"


def test_wrap_text_to_width_empty_text():
    assert wrap_text_to_width("", FONT_PATH, 64, max_width_px=500) == ""
