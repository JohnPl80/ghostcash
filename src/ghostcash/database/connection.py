"""SQLite connection helper.

Foreign keys are OFF by default in SQLite and must be enabled per
connection - this module is the single place that does so, so every
caller gets referential integrity consistently.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open a SQLite connection with sane defaults for GhostCash.

    Creates the parent directory if it doesn't exist yet (fresh installs).
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
