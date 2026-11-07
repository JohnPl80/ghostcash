"""`ghostcash stats` - views, engagement and the product rule for one channel.

Read-only: prints what the daily analytics job stored; contacts no service.
Use `--config config/brands/<key>.yaml` for an extra channel.
"""

from __future__ import annotations

import argparse
import sqlite3

from ghostcash.analytics.stats import build_stats
from ghostcash.config.loader import ConfigError, load_config
from ghostcash.database.connection import get_connection


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1
    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    try:
        print(build_stats(conn, config, top=getattr(args, "top", 10)))
    except sqlite3.OperationalError as exc:
        print(f"[error] database at '{db_path}' is not ready: {exc}")
        print("        run `ghostcash init` to create and migrate it.")
        return 1
    finally:
        conn.close()
    return 0
