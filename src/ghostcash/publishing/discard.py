"""Remove unwanted drafts from Buffer (`ghostcash drafts discard`).

Deleting a draft in Buffer is how a video is rejected (see
analytics/collector.py: content whose every post is gone from Buffer is
retired to REJECTED). Doing that by hand for a pile of 50 videos on two
channels means about a hundred clicks, so this does the same thing from the
command line - carefully:

  - Only content that is waiting as DRAFTS is touched. Anything with a post
    that is scheduled to go out, or already went out, is never selected.
  - Nothing is deleted unless the caller asks for it explicitly; the default
    is a preview that lists what would be removed.
  - The caller picks how many videos to remove in one run (default 1), so
    the very first real deletion can be a single video that is checked in
    Buffer before the rest follow.
  - A post that is already gone from Buffer counts as removed, so an
    interrupted run can simply be started again.
  - Three videos in a row that fail stop the run (a wrong key or an outage
    would otherwise fail a hundred times in a row).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ghostcash.publishing.base import PostNotFoundError, PublisherError
from ghostcash.publishing.buffer import BufferPublisher

# A short pause between deletions keeps well under any request rate limit.
PAUSE_BETWEEN_DELETES_SECONDS = 1.0
MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class DraftPost:
    published_post_id: int
    external_post_id: str
    platform: str


@dataclass
class DiscardCandidate:
    content_id: int
    title: str
    created_at: str
    posts: list[DraftPost]


@dataclass
class DiscardResult:
    # Content ids that are now fully removed from Buffer and marked REJECTED.
    discarded: list[int] = field(default_factory=list)
    posts_deleted: int = 0
    # Posts Buffer no longer had (already deleted by hand or by an earlier run).
    posts_already_gone: int = 0
    # (content id, explanation) for every video that could not be fully removed.
    failures: list[tuple[int, str]] = field(default_factory=list)
    stopped_early: bool = False


def select_discardable_drafts(
    conn: sqlite3.Connection,
    *,
    limit: int | None = None,
    created_before: str | None = None,
) -> list[DiscardCandidate]:
    """Oldest-first content that sits in Buffer only as drafts.

    `created_before` is an ISO date (YYYY-MM-DD): only content created
    before that day is selected, so videos made after a fix can be kept
    while older ones are cleared out.
    """
    sql = (
        "SELECT c.id, c.title, c.topic, c.created_at FROM content c "
        "WHERE c.status = 'SCHEDULED' "
        "AND EXISTS (SELECT 1 FROM published_posts pp WHERE pp.content_id = c.id "
        "AND pp.status = 'DRAFT' AND pp.external_post_id IS NOT NULL) "
        "AND NOT EXISTS (SELECT 1 FROM published_posts pp WHERE pp.content_id = c.id "
        "AND pp.status IN ('SCHEDULED', 'PUBLISHED'))"
    )
    params: list[object] = []
    if created_before:
        sql += " AND c.created_at < ?"
        params.append(created_before)
    sql += " ORDER BY c.id"
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    candidates: list[DiscardCandidate] = []
    for row in conn.execute(sql, params).fetchall():
        post_rows = conn.execute(
            "SELECT pp.id, pp.external_post_id, COALESCE(p.name, '?') AS platform "
            "FROM published_posts pp LEFT JOIN platforms p ON p.id = pp.platform_id "
            "WHERE pp.content_id = ? AND pp.status = 'DRAFT' AND pp.external_post_id IS NOT NULL "
            "ORDER BY pp.id",
            (row["id"],),
        ).fetchall()
        candidates.append(
            DiscardCandidate(
                content_id=row["id"],
                title=row["title"] or row["topic"] or "(untitled)",
                created_at=row["created_at"],
                posts=[
                    DraftPost(
                        published_post_id=post["id"],
                        external_post_id=str(post["external_post_id"]),
                        platform=post["platform"],
                    )
                    for post in post_rows
                ],
            )
        )
    return candidates


def discard_drafts(
    conn: sqlite3.Connection,
    publisher: BufferPublisher,
    candidates: list[DiscardCandidate],
    *,
    sleep: Callable[[float], None] = time.sleep,
    pause_seconds: float = PAUSE_BETWEEN_DELETES_SECONDS,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
) -> DiscardResult:
    """Delete every draft of each candidate in Buffer, then mark the content
    REJECTED so it stops counting towards the queue.

    A video is marked REJECTED only once ALL of its drafts are gone. If one
    channel's draft cannot be deleted the video stays as it was, and a later
    run picks it up again (the drafts that were deleted are then reported as
    already gone).
    """
    result = DiscardResult()
    consecutive_failures = 0
    first_request = True

    for candidate in candidates:
        fully_removed = True
        for post in candidate.posts:
            if not first_request:
                sleep(pause_seconds)
            first_request = False
            try:
                publisher.delete_post(post.external_post_id)
                result.posts_deleted += 1
            except PostNotFoundError:
                result.posts_already_gone += 1
            except PublisherError as exc:
                result.failures.append(
                    (candidate.content_id, f"{post.platform} draft {post.external_post_id}: {exc}")
                )
                fully_removed = False
                break

        if fully_removed:
            conn.execute(
                "UPDATE content SET status = 'REJECTED' WHERE id = ? AND status = 'SCHEDULED'",
                (candidate.content_id,),
            )
            conn.commit()
            result.discarded.append(candidate.content_id)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                result.stopped_early = True
                break

    return result
