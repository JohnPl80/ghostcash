"""Content and content-variant models (CLAUDE.md sections 7 and 30)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any

CONTENT_STATUSES: tuple[str, ...] = (
    "IDEA",
    "RESEARCHING",
    "DRAFT",
    "APPROVED",
    "RENDERING",
    "READY",
    "SCHEDULED",
    "PUBLISHED",
    "ANALYZING",
    "WINNER",
    "REJECTED",
    "FAILED",
)

VARIANT_TYPES: tuple[str, ...] = ("HOOK", "SCRIPT", "CTA", "TOPIC")
VARIANT_STATUSES: tuple[str, ...] = ("PROPOSED", "TESTING", "WINNER", "REJECTED")


def _row_kwargs(row: sqlite3.Row, dataclass_cls: type) -> dict[str, Any]:
    known = {f.name for f in fields(dataclass_cls)}
    return {k: v for k, v in dict(row).items() if k in known}


@dataclass
class Content:
    topic: str
    category: str
    id: int | None = None
    topic_id: int | None = None
    title: str | None = None
    hook: str | None = None
    script: str | None = None
    caption: str | None = None
    cta: str | None = None
    product_id: int | None = None
    content_type: str | None = None
    status: str = "IDEA"
    quality_score: float | None = None
    opportunity_score: float | None = None
    fingerprint: str | None = None
    visual_sequence_json: str | None = None
    suggested_product_description: str | None = None
    rendered_video_path: str | None = None
    rendered_duration_seconds: float | None = None
    created_at: str | None = None
    scheduled_at: str | None = None
    published_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CONTENT_STATUSES:
            raise ValueError(f"Invalid content status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Content:
        return cls(**_row_kwargs(row, cls))


@dataclass
class ContentVariant:
    parent_content_id: int
    variant_type: str
    variant_text: str
    id: int | None = None
    status: str = "PROPOSED"
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.variant_type not in VARIANT_TYPES:
            raise ValueError(f"Invalid variant_type: {self.variant_type!r}")
        if self.status not in VARIANT_STATUSES:
            raise ValueError(f"Invalid variant status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ContentVariant:
        return cls(**_row_kwargs(row, cls))


@dataclass
class ContentPerformance:
    content_id: int
    id: int | None = None
    platform_id: int | None = None
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    # None = this platform does not expose the metric (see
    # database/migrations/0005_nullable_clicks_and_visits.sql) - distinct
    # from 0, which means the platform reported a real zero.
    clicks: int | None = None
    profile_visits: int | None = None
    sales: int = 0
    revenue: float = 0.0
    engagement_rate: float | None = None
    click_rate: float | None = None
    conversion_rate: float | None = None
    revenue_per_view: float | None = None
    revenue_per_1000_views: float | None = None
    winner_score: float | None = None
    collected_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ContentPerformance:
        return cls(**_row_kwargs(row, cls))
