"""`ghostcash dashboard` (CLAUDE.md sections 39-41).

Not in section 42's own CLI list - like `products` in Phase 8, the
dashboard needs a CLI entry point to actually launch it and has no
other natural home; see cli/main.py's module docstring for the same
reasoning applied there.

Starts a local uvicorn server serving the FastAPI app from
ghostcash.dashboard. This is a foreground command you run and leave
open while you glance at the dashboard (section 39: "a lightweight
local dashboard"), then Ctrl+C to stop - not a background service
GitHub Actions needs, so it is deliberately not wired into any
workflow.
"""

from __future__ import annotations

import argparse

import uvicorn

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.dashboard.app import create_app


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    db_path = args.db_path or config.database_path
    app = create_app(config, db_path)

    print(f"GhostCash dashboard: http://{args.host}:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
