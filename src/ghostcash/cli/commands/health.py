"""`ghostcash health` - local health check (CLAUDE.md sections 36, 41).

Thin CLI wrapper: loads config (the one step only this command can do,
since it has `args.config_path`/`args.db_path`), then delegates every
actual check to `ghostcash.health.check_system()`, which is shared with
the Phase 9 dashboard's `/system` page and Overview attention banner -
see that module's docstring for why the checks live there instead of
here.
"""

from __future__ import annotations

import argparse
import os

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.health import check_system


def run(args: argparse.Namespace) -> int:
    issues: list[str] = []
    notes: list[str] = []

    config = None
    try:
        config = load_config(args.config_path)
        notes.append(f"[ok] configuration loaded (database_path={config.database_path})")
    except ConfigError as exc:
        issues.append(f"configuration error: {exc}")

    db_path = args.db_path or (config.database_path if config else None)
    result = check_system(config, db_path)
    notes.extend(result.notes)
    issues.extend(result.issues)

    print("GhostCash health check")
    print("=" * 40)
    for note in notes:
        print(note)
    print()

    if issues:
        print("ATTENTION REQUIRED")
        for i, issue in enumerate(issues, start=1):
            print(f"  {i}. {issue}")
        _annotate_on_github(issues)
        return 1

    print("RUNNING - NOTHING REQUIRED")
    return 0


def _annotate_on_github(issues: list[str]) -> None:
    """In GitHub Actions, also show each issue as an error annotation, so the
    reason a run stopped is visible on the run page, in the failure email's
    annotation list and through the API - not only inside the full log."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for issue in issues:
        text = " ".join(str(issue).split()).replace("%", "%25")
        print(f"::error title=Health check::{text[:900]}")
