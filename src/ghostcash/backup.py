"""SQLite backup + retention (CLAUDE.md section 58).

"Before every major mutation: SQLite backup. Retain: 7 daily backups,
4 weekly backups. Compress backups." This module implements all three.

Backups use sqlite3's online backup API (`Connection.backup()`) rather
than a plain file copy. A plain copy can be taken mid-write - especially
in WAL mode, where the real data can be split across the main file and
a `-wal` file - and would silently produce a corrupt snapshot. The
backup API is the documented, safe way to copy a SQLite database while
it may be in use (https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.backup).

Backups deliberately live outside git (see .gitignore: data/backups/).
orders.customer_email means a backup can contain real customer data
(CLAUDE.md section 27 requires recording the customer on every order) -
that must never land in a public repository's permanent history. See
ghostcash/cli/commands/backup.py and the GitHub Actions workflows for
how these survive across GitHub's ephemeral runners instead (a shared
actions/cache entry, not a git commit).
"""

from __future__ import annotations

import gzip
import shutil
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from ghostcash.config.schema import Config

_FILENAME_PREFIX = "ghostcash-"
_FILENAME_SUFFIX = ".db.gz"
_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


class BackupError(Exception):
    """Raised when a backup cannot be created."""


@dataclass
class BackupResult:
    path: Path
    size_bytes: int
    pruned: list[Path] = field(default_factory=list)


def _timestamp_for(filename: str) -> datetime | None:
    if not (filename.startswith(_FILENAME_PREFIX) and filename.endswith(_FILENAME_SUFFIX)):
        return None
    raw = filename[len(_FILENAME_PREFIX) : -len(_FILENAME_SUFFIX)]
    try:
        return datetime.strptime(raw, _TIMESTAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def list_backups(backup_dir: str | Path) -> list[tuple[datetime, Path]]:
    """Every existing backup in `backup_dir`, oldest first."""
    backup_dir = Path(backup_dir)
    if not backup_dir.exists():
        return []
    found = []
    for path in backup_dir.iterdir():
        ts = _timestamp_for(path.name)
        if ts is not None:
            found.append((ts, path))
    found.sort(key=lambda pair: pair[0])
    return found


def create_backup(db_path: str | Path, backup_dir: str | Path, now: datetime | None = None) -> Path:
    """Create a compressed, consistent snapshot of the live database and
    return its path. Raises BackupError if the source database doesn't
    exist yet (nothing to back up - e.g. before `ghostcash init`)."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise BackupError(f"Database not found at '{db_path}' - nothing to back up.")

    backup_dir = Path(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = (now or datetime.now(UTC)).strftime(_TIMESTAMP_FORMAT)
    uncompressed_path = backup_dir / f".tmp-{timestamp}.db"
    compressed_path = backup_dir / f"{_FILENAME_PREFIX}{timestamp}{_FILENAME_SUFFIX}"

    source = sqlite3.connect(str(db_path))
    try:
        destination = sqlite3.connect(str(uncompressed_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        raise BackupError(f"Could not snapshot '{db_path}': {exc}") from exc
    finally:
        source.close()

    try:
        with uncompressed_path.open("rb") as raw, gzip.open(compressed_path, "wb") as gz:
            shutil.copyfileobj(raw, gz)
    finally:
        uncompressed_path.unlink(missing_ok=True)

    return compressed_path


def prune_backups(
    backup_dir: str | Path,
    daily_retention: int = 7,
    weekly_retention: int = 4,
) -> list[Path]:
    """Enforce "7 daily backups, 4 weekly backups" and return what was
    deleted.

    Algorithm: bucket every backup by calendar date, keep the newest
    backup from each of the most recent `daily_retention` distinct
    days ("daily" tier). Days covered by the daily tier are fully
    retired from further consideration - a second same-day backup
    (e.g. two manual `ghostcash backup` runs) is simply redundant and
    is pruned here, not promoted into the weekly tier. Everything from
    an *older*, not-yet-covered day is then re-bucketed by ISO (year,
    week), keeping the newest backup from each of the next
    `weekly_retention` distinct weeks ("weekly" tier). Anything not
    selected by either tier is deleted.
    """
    backups = list_backups(backup_dir)

    by_date: dict[date, tuple[datetime, Path]] = {}
    for ts, path in backups:
        day = ts.date()
        if day not in by_date or ts > by_date[day][0]:
            by_date[day] = (ts, path)

    daily_days = set(sorted(by_date.keys(), reverse=True)[:daily_retention])
    kept_daily = {by_date[day][1] for day in daily_days}

    by_week: dict[tuple[int, int], tuple[datetime, Path]] = {}
    for ts, path in backups:
        if ts.date() in daily_days:
            continue
        iso_year, iso_week, _ = ts.isocalendar()
        week_key = (iso_year, iso_week)
        if week_key not in by_week or ts > by_week[week_key][0]:
            by_week[week_key] = (ts, path)

    weekly_keys = sorted(by_week.keys(), reverse=True)[:weekly_retention]
    kept_weekly = {by_week[key][1] for key in weekly_keys}

    kept = kept_daily | kept_weekly
    pruned: list[Path] = []
    for _, path in backups:
        if path not in kept:
            path.unlink(missing_ok=True)
            pruned.append(path)
    return pruned


def run_backup(config: Config, db_path: str | Path, now: datetime | None = None) -> BackupResult:
    """Create a backup and prune to the configured retention policy."""
    path = create_backup(db_path, config.backup.backup_dir, now=now)
    pruned = prune_backups(
        config.backup.backup_dir,
        daily_retention=config.backup.daily_retention,
        weekly_retention=config.backup.weekly_retention,
    )
    return BackupResult(path=path, size_bytes=path.stat().st_size, pruned=pruned)
