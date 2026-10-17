"""`ghostcash backup` (CLAUDE.md section 58).

Creates a compressed, consistent snapshot of the live database and
prunes old ones to the configured retention policy. Safe to run at any
time, including against a database being written to concurrently (see
ghostcash/backup.py for why the sqlite3 online backup API is used
instead of a plain file copy).
"""

from __future__ import annotations

import argparse
import traceback

from ghostcash.backup import BackupError, run_backup
from ghostcash.config.loader import ConfigError, load_config
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    job_id = start_job(conn, "backup_database")

    try:
        result = run_backup(config, db_path)
    except BackupError as exc:
        fail_job(conn, job_id, str(exc), traceback.format_exc())
        conn.close()
        print(f"[error] {exc}")
        return 1

    succeed_job(conn, job_id)
    conn.close()

    size_kb = result.size_bytes / 1024
    print(f"[ok] backup written: {result.path} ({size_kb:.1f} KB)")
    if result.pruned:
        print(f"[ok] pruned {len(result.pruned)} backup(s) outside the retention policy:")
        for path in result.pruned:
            print(f"       {path}")
    return 0
