"""Tests for ghostcash.backup (CLAUDE.md section 58).

"Before every major mutation: SQLite backup. Retain: 7 daily backups,
4 weekly backups. Compress backups."
"""

from __future__ import annotations

import gzip
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ghostcash.backup import (
    BackupError,
    create_backup,
    list_backups,
    prune_backups,
    run_backup,
)
from ghostcash.config.schema import Config


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO t (value) VALUES ('hello')")
    conn.commit()
    conn.close()


# -- create_backup -------------------------------------------------------


def test_create_backup_raises_when_database_does_not_exist(tmp_path):
    with pytest.raises(BackupError, match="not found"):
        create_backup(tmp_path / "missing.db", tmp_path / "backups")


def test_create_backup_produces_a_restorable_gzip_snapshot(tmp_path):
    db_path = tmp_path / "ghostcash.db"
    _make_db(db_path)

    backup_path = create_backup(db_path, tmp_path / "backups")

    assert backup_path.exists()
    assert backup_path.name.startswith("ghostcash-")
    assert backup_path.name.endswith(".db.gz")

    # Decompress and open with sqlite3 to prove it's a real, intact
    # database snapshot - not just a renamed/truncated copy.
    restored_path = tmp_path / "restored.db"
    with gzip.open(backup_path, "rb") as gz, restored_path.open("wb") as out:
        out.write(gz.read())
    conn = sqlite3.connect(str(restored_path))
    rows = conn.execute("SELECT value FROM t").fetchall()
    conn.close()
    assert rows == [("hello",)]


def test_create_backup_does_not_leave_a_temp_file_behind(tmp_path):
    db_path = tmp_path / "ghostcash.db"
    _make_db(db_path)
    backup_dir = tmp_path / "backups"

    create_backup(db_path, backup_dir)

    leftovers = [p for p in backup_dir.iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_create_backup_creates_the_backup_dir_if_missing(tmp_path):
    db_path = tmp_path / "ghostcash.db"
    _make_db(db_path)
    backup_dir = tmp_path / "nested" / "backups"

    create_backup(db_path, backup_dir)

    assert backup_dir.is_dir()


# -- list_backups ---------------------------------------------------------


def test_list_backups_returns_empty_list_for_missing_dir(tmp_path):
    assert list_backups(tmp_path / "does-not-exist") == []


def test_list_backups_ignores_files_that_do_not_match_the_naming_scheme(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "ghostcash-20260101T000000Z.db.gz").touch()
    (backup_dir / "readme.txt").touch()
    (backup_dir / ".tmp-20260101T000000Z.db").touch()

    found = list_backups(backup_dir)

    assert len(found) == 1
    assert found[0][1].name == "ghostcash-20260101T000000Z.db.gz"


def test_list_backups_sorted_oldest_first(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "ghostcash-20260103T000000Z.db.gz").touch()
    (backup_dir / "ghostcash-20260101T000000Z.db.gz").touch()
    (backup_dir / "ghostcash-20260102T000000Z.db.gz").touch()

    found = list_backups(backup_dir)

    assert [ts.day for ts, _ in found] == [1, 2, 3]


# -- prune_backups ---------------------------------------------------------


def _touch_backup(backup_dir: Path, ts: datetime) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"ghostcash-{ts.strftime('%Y%m%dT%H%M%SZ')}.db.gz"
    path.touch()
    return path


def test_prune_backups_keeps_everything_within_retention(tmp_path):
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 10, 7, tzinfo=UTC)
    for days_ago in range(5):
        _touch_backup(backup_dir, now - timedelta(days=days_ago))

    pruned = prune_backups(backup_dir, daily_retention=7, weekly_retention=4)

    assert pruned == []
    assert len(list_backups(backup_dir)) == 5


def test_prune_backups_keeps_only_newest_backup_per_day(tmp_path):
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 10, 7, 8, 0, 0, tzinfo=UTC)
    older_same_day = _touch_backup(backup_dir, now.replace(hour=1))
    newer_same_day = _touch_backup(backup_dir, now.replace(hour=20))

    pruned = prune_backups(backup_dir, daily_retention=7, weekly_retention=4)

    assert pruned == [older_same_day]
    remaining = [p for _, p in list_backups(backup_dir)]
    assert remaining == [newer_same_day]


def test_prune_backups_promotes_old_dailies_into_weekly_tier(tmp_path):
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 10, 7, tzinfo=UTC)
    # One backup per day for the last 30 days - far more than
    # daily_retention=7, so everything past the most recent 7 distinct
    # days must survive only if it lands in one of the 4 kept ISO weeks.
    created = [_touch_backup(backup_dir, now - timedelta(days=d)) for d in range(30)]

    pruned = prune_backups(backup_dir, daily_retention=7, weekly_retention=4)

    remaining = {p for _, p in list_backups(backup_dir)}
    assert set(created) - remaining == set(pruned)
    # Never keep more than daily_retention + weekly_retention backups total.
    assert len(remaining) <= 7 + 4
    # The single most recent backup must always survive.
    assert created[0] in remaining


def test_prune_backups_handles_empty_directory(tmp_path):
    assert prune_backups(tmp_path / "backups") == []


def test_prune_backups_zero_retention_deletes_everything(tmp_path):
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 10, 7, tzinfo=UTC)
    created = [_touch_backup(backup_dir, now - timedelta(days=d)) for d in range(3)]

    pruned = prune_backups(backup_dir, daily_retention=0, weekly_retention=0)

    assert set(pruned) == set(created)
    assert list_backups(backup_dir) == []


# -- run_backup (end-to-end) -----------------------------------------------


def test_run_backup_creates_and_prunes_in_one_call(tmp_path):
    db_path = tmp_path / "ghostcash.db"
    _make_db(db_path)
    backup_dir = tmp_path / "backups"

    config = Config()
    config.backup.backup_dir = str(backup_dir)
    config.backup.daily_retention = 7
    config.backup.weekly_retention = 4

    # Seed enough old backups that this run's new one should trigger a prune.
    now = datetime(2026, 10, 7, tzinfo=UTC)
    for days_ago in range(1, 10):
        _touch_backup(backup_dir, now - timedelta(days=days_ago))

    result = run_backup(config, db_path, now=now)

    assert result.path.exists()
    assert result.size_bytes > 0
    assert result.path not in result.pruned
    remaining = {p for _, p in list_backups(backup_dir)}
    assert result.path in remaining
    assert len(remaining) <= 7 + 4
