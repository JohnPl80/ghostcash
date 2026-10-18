"""Publishing queue depth (CLAUDE.md section 22).

Turns "how much READY content plus content still waiting in Buffer exists" into a days-of-runway
estimate against config.publishing.posts_per_day, compared to the
configured buffer targets.

Only reports the numbers - "trigger emergency content generation" and
"send an alert" are left to the caller (nightly workflow / dashboard).
Real alerting needs a notification provider, which doesn't exist yet
(Phase 2+, not started); wiring one in is a natural follow-up.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ghostcash.config.schema import Config


@dataclass
class QueueStatus:
    ready_count: int
    scheduled_count: int
    days_remaining: float
    target_days: int
    emergency_threshold_days: int
    alert_threshold_days: int

    @property
    def is_below_emergency_threshold(self) -> bool:
        return self.days_remaining < self.emergency_threshold_days

    @property
    def is_below_alert_threshold(self) -> bool:
        return self.days_remaining < self.alert_threshold_days


def count_awaiting_publication(conn: sqlite3.Connection) -> int:
    """Content already handed to Buffer whose posts have not gone out yet.

    `content.status` becomes SCHEDULED when the content is sent to Buffer
    and never changes after that (posts going out is recorded per platform
    in `published_posts`). Counting every SCHEDULED row would therefore
    only ever grow - after a week of posting the queue would claim a full
    week of runway while nothing was left, and the "running out of
    content" alarm could never fire. So only content that still has a post
    waiting in Buffer (a draft you have not approved, or a scheduled post
    not yet sent) counts. Content whose posts all went out, failed, or
    were deleted in Buffer (retired to REJECTED by the analytics job) does
    not.
    """
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM content c WHERE c.status = 'SCHEDULED' AND EXISTS ("
            "SELECT 1 FROM published_posts pp WHERE pp.content_id = c.id "
            "AND pp.status IN ('DRAFT', 'SCHEDULED'))"
        ).fetchone()[0]
    )


def get_queue_status(conn: sqlite3.Connection, config: Config) -> QueueStatus:
    ready_count = conn.execute("SELECT COUNT(*) FROM content WHERE status = 'READY'").fetchone()[0]
    scheduled_count = count_awaiting_publication(conn)

    posts_per_day = max(config.publishing.posts_per_day, 1)
    days_remaining = (ready_count + scheduled_count) / posts_per_day

    return QueueStatus(
        ready_count=ready_count,
        scheduled_count=scheduled_count,
        days_remaining=days_remaining,
        target_days=config.queue.target_days,
        emergency_threshold_days=config.queue.emergency_threshold_days,
        alert_threshold_days=config.queue.alert_threshold_days,
    )
