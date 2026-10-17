from __future__ import annotations

from ghostcash.optimization.classify import classify_cta, classify_hook


def test_classify_hook_question():
    assert classify_hook("Are you still doing this manually?") == "QUESTION"


def test_classify_hook_number_list():
    assert classify_hook("3 Excel shortcuts nobody uses") == "NUMBER_LIST"


def test_classify_hook_warning():
    assert classify_hook("Stop making this mistake in Excel") == "WARNING"


def test_classify_hook_statement_fallback():
    assert classify_hook("Excel has a feature most people overlook") == "STATEMENT"


def test_classify_hook_empty_or_none_is_statement():
    assert classify_hook("") == "STATEMENT"
    assert classify_hook(None) == "STATEMENT"


def test_classify_hook_question_beats_number():
    # Ends with "?" and also contains a number - question mark wins
    # (checked first, per the documented priority order).
    assert classify_hook("3 mistakes you're still making?") == "QUESTION"


def test_classify_cta_follow():
    assert classify_cta("Follow for more tips") == "FOLLOW"


def test_classify_cta_comment():
    assert classify_cta("Comment your favorite trick below") == "COMMENT"


def test_classify_cta_link_in_bio():
    assert classify_cta("Link in bio for the full guide") == "LINK_IN_BIO"


def test_classify_cta_save_share():
    assert classify_cta("Save this for later") == "SAVE_SHARE"
    assert classify_cta("Share this with a friend") == "SAVE_SHARE"


def test_classify_cta_other_fallback():
    assert classify_cta("Try it today") == "OTHER"


def test_classify_cta_empty_or_none_is_other():
    assert classify_cta("") == "OTHER"
    assert classify_cta(None) == "OTHER"
