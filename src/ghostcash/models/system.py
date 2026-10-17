"""System-event and settings models (CLAUDE.md sections 38-41, 46, 59)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any

EVENT_SEVERITIES: tuple[str, ...] = ("INFO", "WARNING", "ERROR", "CRITICAL")


def _row_kwargs(row: sqlite3.Row, dataclass_cls: type) -> dict[str, Any]:
    known = {f.name for f in fields(dataclass_cls)}
    return {k: v for k, v in dict(row).items() if k in known}


@dataclass
class SystemEvent:
    event_type: str
    component: str
    id: int | None = None
    severity: str = "INFO"
    message: str | None = None
    metadata_json: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in EVENT_SEVERITIES:
            raise ValueError(f"Invalid event severity: {self.severity!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> SystemEvent:
        return cls(**_row_kwargs(row, cls))


@dataclass
class Setting:
    key: str
    value: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Setting:
        return cls(**_row_kwargs(row, cls))
