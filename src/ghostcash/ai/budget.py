"""Best-effort AI usage tracking (CLAUDE.md section 46).

Records call counts and token usage (as reported by the provider) per
UTC calendar month in the `settings` table, so they persist across CLI
invocations and workflow runs that share a database.

Deliberately NOT included: converting usage into an estimated EUR cost.
Published per-token pricing changes often and varies by model, and a
stale hardcoded price table would be worse than none - silently
reporting a wrong "under budget" is exactly the kind of placeholder
this project avoids. `monthly_budget.ai` in config.yaml is therefore a
stated intent, not yet an automatically-enforced ceiling. The
currently-enforced safety valve is `AI_MAX_REQUESTS_PER_RUN`, checked
by callers before making calls. Wiring in real cost estimation is a
good follow-up once a pricing source is chosen.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


def _month_suffix(when: datetime | None = None) -> str:
    return (when or datetime.now(UTC)).strftime("%Y-%m")


@dataclass
class UsageTracker:
    conn: sqlite3.Connection

    def _get(self, key: str) -> float:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return float(row["value"]) if row and row["value"] is not None else 0.0

    def _set(self, key: str, value: float) -> None:
        now = datetime.now(UTC).isoformat()
        self.conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value), now),
        )
        self.conn.commit()

    def calls_this_month(self) -> int:
        return int(self._get(f"usage.ai.calls:{_month_suffix()}"))

    def prompt_tokens_this_month(self) -> int:
        return int(self._get(f"usage.ai.prompt_tokens:{_month_suffix()}"))

    def output_tokens_this_month(self) -> int:
        return int(self._get(f"usage.ai.output_tokens:{_month_suffix()}"))

    def record_call(self, prompt_tokens: int = 0, output_tokens: int = 0) -> None:
        month = _month_suffix()
        self._set(f"usage.ai.calls:{month}", self.calls_this_month() + 1)
        if prompt_tokens:
            self._set(
                f"usage.ai.prompt_tokens:{month}", self.prompt_tokens_this_month() + prompt_tokens
            )
        if output_tokens:
            self._set(
                f"usage.ai.output_tokens:{month}", self.output_tokens_this_month() + output_tokens
            )
