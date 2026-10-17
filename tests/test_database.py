from __future__ import annotations

import sqlite3

import pytest

from ghostcash.database.connection import get_connection
from ghostcash.database.migrate import apply_migrations, available_migrations, get_applied_versions
from ghostcash.health import REQUIRED_TABLES


def test_available_migrations_finds_initial_migration():
    versions = [p.stem for p in available_migrations()]
    assert "0001_initial" in versions


def test_apply_migrations_creates_all_required_tables(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    try:
        apply_migrations(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        assert missing == [], f"missing tables: {missing}"
    finally:
        conn.close()


def test_apply_migrations_is_idempotent(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    try:
        first_run = apply_migrations(conn)
        second_run = apply_migrations(conn)
        assert first_run == [p.stem for p in available_migrations()]
        assert second_run == []
    finally:
        conn.close()


def test_get_applied_versions_reflects_disk_state(tmp_path):
    conn = get_connection(tmp_path / "test.db")
    try:
        assert get_applied_versions(conn) == set()
        apply_migrations(conn)
        assert get_applied_versions(conn) == {p.stem for p in available_migrations()}
    finally:
        conn.close()


def test_foreign_keys_are_enabled(migrated_conn):
    row = migrated_conn.execute("PRAGMA foreign_keys").fetchone()
    assert row[0] == 1


def test_foreign_key_violation_is_rejected(migrated_conn):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO content (topic, category, topic_id) VALUES (?, ?, ?)",
            ("some topic", "AI", 9999),
        )


def test_content_status_check_constraint_rejects_invalid_status(migrated_conn):
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO content (topic, category, status) VALUES (?, ?, ?)",
            ("some topic", "AI", "NOT_A_REAL_STATUS"),
        )


def test_published_posts_idempotency_key_is_unique(migrated_conn):
    migrated_conn.execute("INSERT INTO content (id, topic, category) VALUES (1, 't', 'AI')")
    migrated_conn.execute("INSERT INTO platforms (id, name) VALUES (1, 'YOUTUBE')")
    migrated_conn.execute(
        "INSERT INTO published_posts (content_id, platform_id, idempotency_key) "
        "VALUES (1, 1, 'dup-key')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated_conn.execute(
            "INSERT INTO published_posts (content_id, platform_id, idempotency_key) "
            "VALUES (1, 1, 'dup-key')"
        )
