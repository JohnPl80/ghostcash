"""Platform / campaign / published-post models (CLAUDE.md sections 19-22, 57)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any

PLATFORM_NAMES: tuple[str, ...] = ("YOUTUBE", "TIKTOK", "INSTAGRAM")
CAMPAIGN_STATUSES: tuple[str, ...] = ("ACTIVE", "PAUSED", "COMPLETED")
PUBLISHED_POST_STATUSES: tuple[str, ...] = ("PENDING", "DRAFT", "SCHEDULED", "PUBLISHED", "FAILED")


def _row_kwargs(row: sqlite3.Row, dataclass_cls: type) -> dict[str, Any]:
    known = {f.name for f in fields(dataclass_cls)}
    return {k: v for k, v in dict(row).items() if k in known}


@dataclass
class Platform:
    name: str
    id: int | None = None
    buffer_channel_id: str | None = None
    is_active: bool = True
    config_json: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.name not in PLATFORM_NAMES:
            raise ValueError(f"Invalid platform name: {self.name!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Platform:
        data = _row_kwargs(row, cls)
        if "is_active" in data:
            data["is_active"] = bool(data["is_active"])
        return cls(**data)


@dataclass
class Campaign:
    name: str
    id: int | None = None
    description: str | None = None
    category: str | None = None
    status: str = "ACTIVE"
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CAMPAIGN_STATUSES:
            raise ValueError(f"Invalid campaign status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Campaign:
        return cls(**_row_kwargs(row, cls))


@dataclass
class PublishedPost:
    content_id: int
    platform_id: int
    idempotency_key: str
    id: int | None = None
    campaign_id: int | None = None
    external_post_id: str | None = None
    buffer_update_id: str | None = None
    status: str = "PENDING"
    scheduled_at: str | None = None
    published_at: str | None = None
    error: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in PUBLISHED_POST_STATUSES:
            raise ValueError(f"Invalid published_post status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> PublishedPost:
        return cls(**_row_kwargs(row, cls))
