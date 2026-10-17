from __future__ import annotations

from ghostcash.config.schema import Config
from ghostcash.content.quality import evaluate_content
from ghostcash.models.content import Content

GOOD_SCRIPT = (
    "Most people never learn keyboard shortcuts in Excel. Pressing Ctrl "
    "plus arrow jumps straight to the edge of your data instantly. That "
    "single habit alone saves hours every single month at work."
)


def _content(**overrides) -> Content:
    defaults = dict(
        topic="Excel shortcuts",
        category="EXCEL",
        hook="You are wasting hours in Excel every week.",
        script=GOOD_SCRIPT,
        caption="The shortcut nobody teaches you.",
        cta="Follow for more Excel tips.",
    )
    defaults.update(overrides)
    return Content(**defaults)


def test_good_content_passes(migrated_conn):
    result = evaluate_content(_content(), migrated_conn, Config())
    assert result.passed is True
    assert result.quality_score >= Config().quality_score_threshold
    assert result.fingerprint


def test_empty_script_is_rejected(migrated_conn):
    result = evaluate_content(_content(script=""), migrated_conn, Config())
    assert result.passed is False
    assert result.quality_score == 0
    assert any("script is empty" in r for r in result.reasons)


def test_missing_hook_is_rejected(migrated_conn):
    result = evaluate_content(_content(hook=""), migrated_conn, Config())
    assert result.passed is False
    assert any("hook is missing" in r for r in result.reasons)


def test_missing_cta_is_rejected(migrated_conn):
    result = evaluate_content(_content(cta=""), migrated_conn, Config())
    assert result.passed is False
    assert any("CTA is missing" in r for r in result.reasons)


def test_script_too_long_is_rejected(migrated_conn):
    config = Config()
    config.content.max_script_words = 5
    result = evaluate_content(_content(), migrated_conn, config)
    assert result.passed is False
    assert any("exceeds configured length" in r for r in result.reasons)


def test_excessive_repetition_is_rejected(migrated_conn):
    repetitive_script = " ".join(["shortcut"] * 30)
    result = evaluate_content(_content(script=repetitive_script), migrated_conn, Config())
    assert result.passed is False
    assert any("excessive repetition" in r for r in result.reasons)


def test_near_duplicate_of_existing_content_is_rejected(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO content (topic, category, script) VALUES (?, ?, ?)",
        ("existing", "EXCEL", GOOD_SCRIPT),
    )
    migrated_conn.commit()

    result = evaluate_content(_content(), migrated_conn, Config())

    assert result.passed is False
    assert result.duplicate_of_content_id is not None
    assert any("too similar to existing content" in r for r in result.reasons)


def test_evaluating_an_already_inserted_row_excludes_itself(migrated_conn):
    content = _content()
    cursor = migrated_conn.execute(
        "INSERT INTO content (topic, category, script, hook, cta) VALUES (?, ?, ?, ?, ?)",
        (content.topic, content.category, content.script, content.hook, content.cta),
    )
    migrated_conn.commit()
    content.id = cursor.lastrowid

    result = evaluate_content(content, migrated_conn, Config())

    # Should not flag itself as a duplicate of itself.
    assert result.duplicate_of_content_id is None


def test_invalid_product_link_is_rejected(migrated_conn):
    migrated_conn.execute("INSERT INTO products (id, name, url) VALUES (1, 'Guide', '')")
    migrated_conn.commit()

    result = evaluate_content(_content(product_id=1), migrated_conn, Config())

    assert result.passed is False
    assert any("product link is invalid" in r for r in result.reasons)


def test_valid_product_link_does_not_block(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO products (id, name, url) VALUES (1, 'Guide', 'https://example.com/guide')"
    )
    migrated_conn.commit()

    result = evaluate_content(_content(product_id=1), migrated_conn, Config())

    assert result.passed is True


def test_missing_product_row_is_rejected(migrated_conn):
    result = evaluate_content(_content(product_id=999), migrated_conn, Config())
    assert result.passed is False
    assert any("product link is invalid" in r for r in result.reasons)


def test_unsupported_claim_phrasing_deducts_score_but_is_not_a_hard_gate(migrated_conn):
    script_with_claim = GOOD_SCRIPT + " Studies show this always works for everyone."
    result = evaluate_content(_content(script=script_with_claim), migrated_conn, Config())
    # Soft deduction, not an automatic reject - see quality.py's docstring
    # for why "unsupported claims" isn't a hard gate here.
    assert result.quality_score < 100
    assert any("unsupported-claim" in r for r in result.reasons)


def test_short_hook_and_missing_caption_reduce_score(migrated_conn):
    result = evaluate_content(_content(hook="Hi", caption=""), migrated_conn, Config())
    assert result.quality_score < 100
