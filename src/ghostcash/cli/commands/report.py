"""`ghostcash report` (CLAUDE.md section 42 CLI list; section 33 step 13
"generate daily report").

Prints the same data the dashboard's Overview page shows
(ghostcash.dashboard.queries), as plain text - meant for a human
skimming CI logs or a terminal, and as the natural foundation for the
NotificationProvider section 38 describes (not built yet - Phase 2+,
not started) to send instead of printing. Has no dependency on FastAPI
or the dashboard actually running; it imports only `queries`, the
data-gathering layer the dashboard also uses.

`print_report()` is the reusable half - `ghostcash run` (section 62's
end-to-end flow ends in "report") calls it directly with a connection
it keeps open for its own further use, rather than shelling out to a
separate `ghostcash report` process.
"""

from __future__ import annotations

import argparse
import sqlite3

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.config.schema import Config
from ghostcash.dashboard import queries
from ghostcash.database.connection import get_connection


def print_report(conn: sqlite3.Connection, config: Config, db_path: str) -> int:
    """Print the full plain-text report to stdout. Returns 0 on success,
    1 if the database isn't ready yet (prints a clear message instead of
    raising a bare sqlite3.Error). Does not close `conn` - that's the
    caller's responsibility, since a caller like `ghostcash run` still
    needs the connection afterward."""
    try:
        status = queries.get_system_status(conn, config, db_path)
        revenue = queries.get_revenue_summary(conn)
        orders = queries.get_order_counts(conn)
        content_counts = queries.get_content_counts(conn)
        top_content = queries.get_top_content(conn, limit=10)
        top_categories = queries.get_top_categories(conn, limit=5)
        top_products = queries.get_top_products(conn, limit=5)
        failed_jobs = queries.get_failed_jobs(conn, limit=5)
    except sqlite3.Error as exc:
        print(f"[error] database at '{db_path}' is not ready: {exc}")
        print("        run `ghostcash init` to create and migrate it.")
        return 1

    print("GhostCash daily report")
    print("=" * 40)
    if status.ok:
        print("SYSTEM STATUS: RUNNING - NOTHING REQUIRED")
    else:
        print("SYSTEM STATUS: ATTENTION REQUIRED")
        for i, item in enumerate(status.attention_items, start=1):
            print(f"  {i}. {item}")

    print()
    print(f"Revenue today:       {revenue.today:.2f}")
    print(f"Revenue this week:   {revenue.this_week:.2f}")
    print(f"Revenue this month:  {revenue.this_month:.2f}")
    print(f"Orders:              {orders.completed} completed / {orders.total} total")
    print(f"Content published:   {content_counts.published}")
    print(f"Content queued:      {content_counts.queued}")

    print()
    print("Top content (by Winner Score):")
    if top_content:
        for row in top_content:
            topic = (row["topic"] or "")[:60]
            print(f"  #{row['id']:<5} [{row['category']:<14}] {topic} - {row['winner_score']:.2f}")
    else:
        print("  (none yet)")

    print()
    print("Top categories (by revenue, else views):")
    if top_categories:
        for row in top_categories:
            print(
                f"  {row['category']:<16} revenue={row['total_revenue']:.2f}  "
                f"views={row['total_views']}"
            )
    else:
        print("  (none yet)")

    print()
    print("Top products:")
    if top_products:
        for row in top_products:
            print(
                f"  {row['name']:<30} status={row['status']:<10} revenue={row['total_revenue']:.2f}"
            )
    else:
        print("  (none yet)")

    print()
    print("Failed jobs (most recent):")
    if failed_jobs:
        for row in failed_jobs:
            print(f"  #{row['id']} {row['job_type']} - {(row['error'] or '')[:80]}")
    else:
        print("  (none)")

    return 0


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    # load_config() falls back to config.example.yaml when config.yaml
    # doesn't exist yet, so it alone doesn't prove the database has ever
    # been created/migrated (`ghostcash init` does both) - print_report()
    # itself turns the resulting sqlite3.OperationalError ("no such
    # table") into a clear, actionable message instead (CLAUDE.md
    # section 37: every failure observable).
    db_path = args.db_path or config.database_path
    conn = get_connection(db_path)
    try:
        return print_report(conn, config, db_path)
    finally:
        conn.close()
