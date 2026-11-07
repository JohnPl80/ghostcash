"""Summarise saved database backups (read-only): for each backup file under
the given folders, print its size and what the database inside holds -
number of content items, highest content id, newest item, posts sent.

    python scripts/inspect_backups.py data/backups [more folders...]

Used by the manual Stats Report workflow to see how a channel's saved
database has changed over time. Never modifies anything: each backup is
decompressed into a temporary file that is deleted afterwards.
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def summarise(path: Path) -> str:
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        with gzip.open(path, "rb") as source:
            shutil.copyfileobj(source, tmp)
        tmp.flush()
        conn = sqlite3.connect(f"file:{tmp.name}?mode=ro", uri=True)
        try:
            n, max_id, newest = conn.execute(
                "SELECT COUNT(*), MAX(id), MAX(created_at) FROM content"
            ).fetchone()
            sent = conn.execute(
                "SELECT COUNT(*) FROM published_posts WHERE status = 'PUBLISHED'"
            ).fetchone()[0]
            measured = conn.execute(
                "SELECT COALESCE(SUM(views), 0) FROM content_performance"
            ).fetchone()[0]
        except sqlite3.Error as exc:
            return f"unreadable ({exc})"
        finally:
            conn.close()
    return (
        f"content {n:>4}, max id {max_id or 0:>4}, newest {(newest or '-')[:16]}, "
        f"posts sent {sent:>4}, views {measured:>6}"
    )


def main(folders: list[str]) -> int:
    for folder in folders:
        root = Path(folder)
        print(f"== {root}")
        files = sorted(root.glob("ghostcash-*.db.gz"))
        if not files:
            print("   (no backups)")
        for path in files:
            size_kb = path.stat().st_size // 1024
            print(f"   {path.name}  {size_kb:>6} KB  {summarise(path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["data/backups"]))
