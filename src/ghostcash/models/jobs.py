"""Job / job-error models (CLAUDE.md section 37 - failure management)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any

JOB_STATUSES: tuple[str, ...] = ("PENDING", "RUNNING", "SUCCEEDED", "FAILED")

# CLAUDE.md section 37: 30s, 2min, 10min, max 3 retries.
DEFAULT_RETRY_DELAYS_SECONDS: tuple[int, ...] = (30, 120, 600)
DEFAULT_MAX_RETRIES = 3


def _row_kwargs(row: sqlite3.Row, dataclass_cls: type) -> dict[str, Any]:
    known = {f.name for f in fields(dataclass_cls)}
    return {k: v for k, v in dict(row).items() if k in known}


@dataclass
class Job:
    job_type: str
    id: int | None = None
    idempotency_key: str | None = None
    status: str = "PENDING"
    attempt: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    stack_trace: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in JOB_STATUSES:
            raise ValueError(f"Invalid job status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        return cls(**_row_kwargs(row, cls))


@dataclass
class JobError:
    job_id: int
    attempt: int
    id: int | None = None
    error_message: str | None = None
    stack_trace: str | None = None
    occurred_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> JobError:
        return cls(**_row_kwargs(row, cls))
