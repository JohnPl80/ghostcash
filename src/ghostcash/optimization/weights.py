"""Learned content-selection weights (CLAUDE.md sections 31, 50:
"update category weights" / "update hook weights" / "update template
weights").

Persisted as JSON in the `settings` table under three keys, the same
pattern ai/budget.py already uses for monthly usage counters - no new
table needed for what is just a handful of small key/value blobs.

A multiplier of 1.0 means "no adjustment yet" (the default for any
value with no data, or not enough of it). Multipliers are blended with
an exponential moving average (`weight_smoothing_alpha`) rather than
replaced outright each run, so one unusually good or bad week on a
small sample can't whipsaw future content selection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

_CATEGORY_KEY = "optimization.category_weights"
_HOOK_TYPE_KEY = "optimization.hook_type_weights"
_TEMPLATE_KEY = "optimization.template_weights"


def _get_json(conn: sqlite3.Connection, key: str) -> dict[str, float]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None or row["value"] is None:
        return {}
    try:
        data = json.loads(row["value"])
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): float(v) for k, v in data.items()}


def _set_json(conn: sqlite3.Connection, key: str, value: dict[str, float]) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, json.dumps(value), now),
    )
    conn.commit()


def get_category_weights(conn: sqlite3.Connection) -> dict[str, float]:
    return _get_json(conn, _CATEGORY_KEY)


def get_hook_type_weights(conn: sqlite3.Connection) -> dict[str, float]:
    return _get_json(conn, _HOOK_TYPE_KEY)


def get_template_weights(conn: sqlite3.Connection) -> dict[str, float]:
    return _get_json(conn, _TEMPLATE_KEY)


def top_value(weights: dict[str, float]) -> str | None:
    """The dimension value with the highest learned multiplier, or None
    if nothing has been learned yet (an empty dict, or every value tied
    at the bottom of a freshly-initialized set - callers treat None as
    "no preference, don't hint anything"). Used to build content
    generation's "prefer this hook type/template" prompt hint."""
    if not weights:
        return None
    return max(weights, key=lambda k: weights[k])


def _blend(
    previous: dict[str, float], observed: dict[str, float], alpha: float
) -> dict[str, float]:
    """EMA-blend this run's `observed` multipliers into `previous`,
    keeping any value not observed this run unchanged (e.g. a category
    with too few samples this week keeps its last known weight rather
    than snapping back to 1.0)."""
    blended = dict(previous)
    for key, new_value in observed.items():
        old_value = previous.get(key, 1.0)
        blended[key] = (1 - alpha) * old_value + alpha * new_value
    return blended


def update_weights(
    conn: sqlite3.Connection,
    *,
    category_observations: dict[str, float],
    hook_type_observations: dict[str, float],
    template_observations: dict[str, float],
    alpha: float,
) -> dict[str, dict[str, float]]:
    """Blend this run's observed performance multipliers into the
    persisted weights for all three dimensions CLAUDE.md section 50
    names, persist the result, and return
    {"category": {...}, "hook_type": {...}, "template": {...}} for the
    caller (cli/commands/optimize.py) to report."""
    updated = {
        "category": _blend(get_category_weights(conn), category_observations, alpha),
        "hook_type": _blend(get_hook_type_weights(conn), hook_type_observations, alpha),
        "template": _blend(get_template_weights(conn), template_observations, alpha),
    }
    _set_json(conn, _CATEGORY_KEY, updated["category"])
    _set_json(conn, _HOOK_TYPE_KEY, updated["hook_type"])
    _set_json(conn, _TEMPLATE_KEY, updated["template"])
    return updated
