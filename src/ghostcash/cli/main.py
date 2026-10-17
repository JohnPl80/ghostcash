"""GhostCash CLI entry point (CLAUDE.md section 42).

Implemented: `init`, `health`, `generate` (topics + scripts - see
ghostcash.content), `render` (see ghostcash.video), `publish` and
`analytics` (see ghostcash.publishing / ghostcash.analytics), `products`
(see ghostcash.products), `report`, `dashboard` (see ghostcash.dashboard),
`queue` (see ghostcash.publishing.queue), `backup` (see ghostcash.backup),
`optimize` (see ghostcash.optimization), and `run` (see
ghostcash.cli.commands.run - the full pipeline in one command, section
62's Definition of Done). Every command CLAUDE.md section 42 names is
now real: `init`, `health`, `generate`, `render`, `publish`,
`analytics`, `optimize`, `report`, `run` (+ `run --dry-run`). `products`,
`dashboard`, `queue`, and `backup` are the only ones NOT in section 42's
own list - `products` was added to carry section 24's human-approval
workflow (detect/list/approve/reject/link/sync-sales), `dashboard` to
launch the Phase 9 web dashboard (sections 39-41), and `queue`/`backup`
to give the Phase 10 GitHub Actions workflows (sections 22, 33, 58) a
real command to call instead of reimplementing that logic in YAML;
none has a section-42 verb to reuse, so each got its own command - see
the relevant cli/commands/*.py docstring. There is no longer an
UNIMPLEMENTED_COMMANDS table - nothing is left unimplemented.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections.abc import Callable

from ghostcash import __version__
from ghostcash.cli.commands import analytics as analytics_cmd
from ghostcash.cli.commands import backup as backup_cmd
from ghostcash.cli.commands import dashboard as dashboard_cmd
from ghostcash.cli.commands import generate as generate_cmd
from ghostcash.cli.commands import health as health_cmd
from ghostcash.cli.commands import init as init_cmd
from ghostcash.cli.commands import optimize as optimize_cmd
from ghostcash.cli.commands import products as products_cmd
from ghostcash.cli.commands import publish as publish_cmd
from ghostcash.cli.commands import queue as queue_cmd
from ghostcash.cli.commands import render as render_cmd
from ghostcash.cli.commands import report as report_cmd
from ghostcash.cli.commands import run as run_cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ghostcash",
        description="GhostCash: autonomous faceless content -> revenue system.",
    )
    parser.add_argument("--version", action="version", version=f"ghostcash {__version__}")
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="Path to config.yaml (default: config/config.yaml, falling back to "
        "config/config.example.yaml)",
    )
    parser.add_argument(
        "--db-path",
        dest="db_path",
        default=None,
        help="Override the database path from config/env for this invocation",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("init", help="Create .env/config.yaml and apply database migrations")
    subparsers.add_parser("health", help="Check configuration, database, and disk health")

    generate_parser = subparsers.add_parser(
        "generate", help="Generate topics, then scripts, using the configured AI provider"
    )
    generate_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override content.topics_per_run for this invocation",
    )
    generate_parser.add_argument(
        "--content-count",
        type=int,
        default=None,
        help="Override content.content_per_run for this invocation",
    )

    render_parser = subparsers.add_parser("render", help="Render approved content into MP4 videos")
    render_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override video.render_per_run for this invocation",
    )

    publish_parser = subparsers.add_parser(
        "publish", help="Sync Buffer channels and publish READY content"
    )
    publish_parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Override content.content_per_run for this invocation",
    )

    subparsers.add_parser("analytics", help="Collect Buffer post metrics and evaluate Winner Score")

    subparsers.add_parser(
        "queue", help="Report publishing queue depth against the configured buffer targets"
    )

    subparsers.add_parser(
        "backup", help="Create a compressed database backup and prune to the retention policy"
    )

    optimize_parser = subparsers.add_parser(
        "optimize",
        help="Update learned content weights, replicate winners, and evaluate experiments",
    )
    optimize_parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if the configured optimization.interval_days hasn't elapsed yet",
    )

    products_parser = subparsers.add_parser(
        "products", help="Detect, approve, link, and track digital product opportunities"
    )
    products_sub = products_parser.add_subparsers(dest="products_action", required=True)

    detect_parser = products_sub.add_parser(
        "detect", help="Find under-monetized topics and propose a product for each"
    )
    detect_parser.add_argument(
        "--count", type=int, default=None, help="Max opportunities to propose (default: 5)"
    )

    list_parser = products_sub.add_parser("list", help="List product opportunities or products")
    list_parser.add_argument(
        "--status",
        default="PENDING_APPROVAL",
        help="Opportunity status to filter by, or 'all' (default: PENDING_APPROVAL)",
    )
    list_parser.add_argument(
        "--products", action="store_true", help="List existing Product rows instead"
    )

    approve_parser = products_sub.add_parser("approve", help="Approve a pending opportunity")
    approve_parser.add_argument("opportunity_id", type=int)

    reject_parser = products_sub.add_parser("reject", help="Reject a pending opportunity")
    reject_parser.add_argument("opportunity_id", type=int)

    link_parser = products_sub.add_parser(
        "link", help="Attach an approved opportunity to a product you created on Gumroad"
    )
    link_parser.add_argument("opportunity_id", type=int)
    link_parser.add_argument("external_product_id", help="Gumroad product id or permalink")

    products_sub.add_parser(
        "sync-sales", help="Pull new Gumroad sales into `orders` and attribute revenue"
    )

    subparsers.add_parser(
        "report", help="Print a plain-text daily report (revenue, content, jobs, system status)"
    )

    dashboard_parser = subparsers.add_parser(
        "dashboard", help="Launch the local web dashboard (FastAPI + simple HTML)"
    )
    dashboard_parser.add_argument(
        "--host", default="127.0.0.1", help="Interface to bind (default: 127.0.0.1)"
    )
    dashboard_parser.add_argument(
        "--port", type=int, default=8000, help="Port to bind (default: 8000)"
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run the full pipeline once, end to end (section 62's Definition of Done)",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Mandatory before any external publishing (section 43) - generates and "
        "renders for real, but never calls Buffer, GitHub Releases, or Gumroad",
    )
    run_parser.add_argument(
        "--topics-count",
        type=int,
        default=None,
        dest="topics_count",
        help="Override content.topics_per_run for this invocation",
    )
    run_parser.add_argument(
        "--content-count",
        type=int,
        default=None,
        dest="content_count",
        help="Override content.content_per_run for this invocation",
    )

    return parser


def _run_against_database(
    command_fn: Callable[[argparse.Namespace], int], args: argparse.Namespace
) -> int:
    """Run a command that assumes an already-migrated database, turning an
    unmigrated one into a clean message instead of a raw traceback.

    `get_connection()` happily creates an empty SQLite file if none
    exists yet (that's sqlite3's own behavior, not a bug), so a command
    run before `ghostcash init` doesn't fail until its first query hits
    a missing table - `sqlite3.OperationalError: no such table: jobs`.
    `report`/`dashboard` already guard against this themselves (Phase 9);
    this is the same fix applied once, centrally, for every other
    command that touches the database.
    """
    try:
        return command_fn(args)
    except sqlite3.Error as exc:
        print(f"[error] database problem: {exc}")
        print("        if this is a fresh checkout, run `ghostcash init` first.")
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return init_cmd.run(args)
    if args.command == "health":
        return health_cmd.run(args)
    if args.command == "generate":
        return _run_against_database(generate_cmd.run, args)
    if args.command == "render":
        return _run_against_database(render_cmd.run, args)
    if args.command == "publish":
        return _run_against_database(publish_cmd.run, args)
    if args.command == "analytics":
        return _run_against_database(analytics_cmd.run, args)
    if args.command == "queue":
        return _run_against_database(queue_cmd.run, args)
    if args.command == "backup":
        return _run_against_database(backup_cmd.run, args)
    if args.command == "optimize":
        return _run_against_database(optimize_cmd.run, args)
    if args.command == "products":
        return _run_against_database(products_cmd.run, args)
    if args.command == "report":
        return report_cmd.run(args)
    if args.command == "dashboard":
        return dashboard_cmd.run(args)
    if args.command == "run":
        return _run_against_database(run_cmd.run, args)

    parser.error(f"Unknown command: {args.command}")
    return 2  # pragma: no cover - argparse.error() already exits


if __name__ == "__main__":
    sys.exit(main())
