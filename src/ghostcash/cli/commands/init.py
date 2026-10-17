"""`ghostcash init` - one-time local setup (CLAUDE.md section 53).

Creates .env and config/config.yaml from their example templates if they
don't already exist, then applies database migrations. Never overwrites
existing files. Safe to run more than once.
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.database.connection import get_connection
from ghostcash.database.migrate import apply_migrations

ENV_EXAMPLE = Path(".env.example")
ENV_FILE = Path(".env")
CONFIG_EXAMPLE = Path("config/config.example.yaml")
CONFIG_FILE = Path("config/config.yaml")


def _copy_if_missing(source: Path, dest: Path) -> str:
    if dest.exists():
        return f"[skip] {dest} already exists"
    if not source.exists():
        return f"[warn] {source} not found - could not create {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return f"[ok]   created {dest} from {source}"


def run(args: argparse.Namespace) -> int:
    print("GhostCash init")
    print("=" * 40)

    print(_copy_if_missing(ENV_EXAMPLE, ENV_FILE))
    print(_copy_if_missing(CONFIG_EXAMPLE, CONFIG_FILE))

    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    try:
        applied = apply_migrations(conn)
    finally:
        conn.close()

    if applied:
        print(f"[ok]   applied migrations: {', '.join(applied)}")
    else:
        print(f"[skip] database at {db_path} is already up to date")

    print()
    print("Remaining manual setup steps:")
    print("  1. Edit .env and set AI_API_KEY (see README.md 'External accounts').")
    print("  2. Add BUFFER_API_KEY once you reach the publishing phase.")
    print("  3. Add GUMROAD_API_KEY once you reach the product phase.")
    print("  4. Review config/config.yaml (scoring weights, budgets, approval modes).")
    print("  Run `ghostcash health` at any time to check current status.")
    return 0
