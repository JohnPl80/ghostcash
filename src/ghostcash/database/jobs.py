"""Job lifecycle helpers backed by the `jobs` / `job_errors` tables
(CLAUDE.md section 37).

Every CLI command or scheduled operation that does real work should
wrap itself with start_job/succeed_job/fail_job so failures are always
observable, per section 69 ("Every failure must be observable").
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime


def start_job(conn: sqlite3.Connection, job_type: str, idempotency_key: str | None = None) -> int:
    now = datetime.now(UTC).isoformat()
    cur = conn.execute(
        "INSERT INTO jobs (job_type, idempotency_key, status, attempt, started_at) "
        "VALUES (?, ?, 'RUNNING', 1, ?)",
        (job_type, idempotency_key, now),
    )
    conn.commit()
    job_id = cur.lastrowid
    assert job_id is not None  # AUTOINCREMENT guarantees this
    return job_id


def succeed_job(conn: sqlite3.Connection, job_id: int) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE jobs SET status = 'SUCCEEDED', finished_at = ? WHERE id = ?", (now, job_id)
    )
    conn.commit()


def fail_job(
    conn: sqlite3.Connection, job_id: int, error: str, stack_trace: str | None = None
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE jobs SET status = 'FAILED', finished_at = ?, error = ?, "
        "stack_trace = ? WHERE id = ?",
        (now, error, stack_trace, job_id),
    )
    conn.execute(
        "INSERT INTO job_errors (job_id, attempt, error_message, stack_trace) VALUES (?, 1, ?, ?)",
        (job_id, error, stack_trace),
    )
    conn.commit()
