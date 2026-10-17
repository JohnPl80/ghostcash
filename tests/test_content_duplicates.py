from __future__ import annotations

from ghostcash.content.duplicates import compute_fingerprint, find_near_duplicate, normalize_text


def test_normalize_text_lowercases_and_strips_punctuation():
    assert normalize_text("Hello, World!!!") == "hello world"


def test_normalize_text_collapses_whitespace():
    assert normalize_text("a   b\n\tc") == "a b c"


def test_compute_fingerprint_is_deterministic():
    assert compute_fingerprint("Hello world") == compute_fingerprint("Hello world")


def test_compute_fingerprint_ignores_case_and_punctuation():
    assert compute_fingerprint("Hello, World!") == compute_fingerprint("hello world")


def test_compute_fingerprint_differs_for_different_text():
    assert compute_fingerprint("Hello world") != compute_fingerprint("Goodbye world")


def _insert_content_with_script(conn, script: str) -> int:
    cursor = conn.execute(
        "INSERT INTO content (topic, category, script) VALUES (?, ?, ?)",
        ("t", "AI", script),
    )
    conn.commit()
    return cursor.lastrowid


def test_find_near_duplicate_detects_high_similarity(migrated_conn):
    original = (
        "Most people never learn keyboard shortcuts in Excel. Pressing "
        "Ctrl+Arrow jumps to the edge of your data instantly."
    )
    existing_id = _insert_content_with_script(migrated_conn, original)

    near_duplicate = (
        "Most people never learn keyboard shortcuts in Excel. Pressing "
        "Ctrl+Arrow jumps straight to the edge of your data instantly."
    )
    result = find_near_duplicate(migrated_conn, near_duplicate, similarity_threshold=0.85)

    assert result == existing_id


def test_find_near_duplicate_returns_none_for_dissimilar_script(migrated_conn):
    _insert_content_with_script(migrated_conn, "A completely unrelated script about cooking pasta.")
    result = find_near_duplicate(
        migrated_conn,
        "Ten psychology facts that will blow your mind today.",
        similarity_threshold=0.85,
    )
    assert result is None


def test_find_near_duplicate_excludes_given_content_id(migrated_conn):
    script = "Exact same script text used for self-comparison test."
    content_id = _insert_content_with_script(migrated_conn, script)

    result = find_near_duplicate(
        migrated_conn, script, similarity_threshold=0.85, exclude_content_id=content_id
    )

    assert result is None


def test_find_near_duplicate_returns_none_on_empty_db(migrated_conn):
    result = find_near_duplicate(
        migrated_conn, "Any script text at all.", similarity_threshold=0.85
    )
    assert result is None
