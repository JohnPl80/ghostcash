"""Topic models (CLAUDE.md sections 11, 12, 49)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any

FRESHNESS_VALUES: tuple[str, ...] = ("EVERGREEN", "TRENDING", "TIME_SENSITIVE")
TOPIC_STATUSES: tuple[str, ...] = ("CANDIDATE", "SELECTED", "REJECTED", "USED")


def _row_kwargs(row: sqlite3.Row, dataclass_cls: type) -> dict[str, Any]:
    known = {f.name for f in fields(dataclass_cls)}
    return {k: v for k, v in dict(row).items() if k in known}


@dataclass
class Topic:
    topic: str
    category: str
    id: int | None = None
    hook: str | None = None
    target_audience: str | None = None
    freshness: str = "EVERGREEN"
    expires_at: str | None = None
    evergreen_score: float | None = None
    curiosity_score: float | None = None
    shareability_score: float | None = None
    commercial_score: float | None = None
    production_cost_score: float | None = None
    competition_score: float | None = None
    overall_score: float | None = None
    status: str = "CANDIDATE"
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.freshness not in FRESHNESS_VALUES:
            raise ValueError(f"Invalid freshness: {self.freshness!r}")
        if self.status not in TOPIC_STATUSES:
            raise ValueError(f"Invalid topic status: {self.status!r}")
        if self.freshness == "TIME_SENSITIVE" and not self.expires_at:
            raise ValueError("TIME_SENSITIVE topics must have an expires_at (CLAUDE.md section 49)")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Topic:
        return cls(**_row_kwargs(row, cls))


@dataclass
class TopicScore:
    topic_id: int
    id: int | None = None
    curiosity_score: float | None = None
    shareability_score: float | None = None
    commercial_score: float | None = None
    evergreen_score: float | None = None
    usefulness_score: float | None = None
    production_simplicity_score: float | None = None
    competition_penalty: float | None = None
    overall_score: float | None = None
    weights_json: str | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> TopicScore:
        return cls(**_row_kwargs(row, cls))
