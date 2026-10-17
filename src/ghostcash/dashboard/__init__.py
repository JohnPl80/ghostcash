"""Lightweight local dashboard (CLAUDE.md sections 39-41).

`app.create_app()` builds the FastAPI app; `queries` gathers the data
(shared with `ghostcash report`); `render` turns it into plain HTML.
Launched via `ghostcash dashboard` (cli/commands/dashboard.py).
"""

from __future__ import annotations

from ghostcash.dashboard.app import create_app

__all__ = ["create_app"]
