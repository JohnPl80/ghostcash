"""Minimal, dependency-free SQLite migration runner.

Migrations are plain numbered .sql files in database/migrations/, applied
in filename order exactly once. Applied versions are tracked in a
schema_migrations table so re-running is always safe (CLAUDE.md
section 56-57: scheduled jobs must be idempotent).
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def ensure_schema_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def get_applied_versions(conn: sqlite3.Connection) -> set[str]:
    ensure_schema_migrations_table(conn)
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def available_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> list[Path]:
    return sorted(migrations_dir.glob("*.sql"))


def apply_migrations(conn: sqlite3.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply any migrations not yet recorded in schema_migrations.

    Returns the list of newly-applied version identifiers (empty if the
    database was already up to date).
    """
    applied = get_applied_versions(conn)
    newly_applied: list[str] = []

    for path in available_migrations(migrations_dir):
        version = path.stem
        if version in applied:
            continue

        sql = path.read_text(encoding="utf-8")
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        newly_applied.append(version)

    return newly_applied
