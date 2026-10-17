"""Publishing orchestration (CLAUDE.md sections 19-22).

publish_batch() uploads each READY content item's video once (media
host), then attempts a post on every active platform, recording one
published_posts row per (content, platform) pair keyed by a stable
idempotency key - a re-run never double-publishes (section 57), and a
failure on one platform doesn't block the others (section 21).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ghostcash.config.schema import Config
from ghostcash.models.content import Content
from ghostcash.models.publishing import Platform
from ghostcash.publishing.base import Publisher, PublisherError, ScheduleResult
from ghostcash.publishing.media_host import MediaHost, MediaHostError


class PublishingSetupError(Exception):
    """Raised when publish_batch() can't even get started (e.g. no
    active platforms configured yet)."""


@dataclass
class PublishOutcome:
    content: Content
    platform: Platform
    result: ScheduleResult | None
    error: str | None


def select_ready_content(conn: sqlite3.Connection, limit: int) -> list[Content]:
    rows = conn.execute(
        "SELECT * FROM content WHERE status = 'READY' AND rendered_video_path IS NOT NULL "
        "ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Content.from_row(row) for row in rows]


def load_active_platforms(conn: sqlite3.Connection) -> list[Platform]:
    rows = conn.execute(
        "SELECT * FROM platforms WHERE is_active = 1 AND buffer_channel_id IS NOT NULL"
    ).fetchall()
    return [Platform.from_row(row) for row in rows]


def _idempotency_key(content_id: int, platform_name: str) -> str:
    return f"content:{content_id}:platform:{platform_name}"


def _existing_published_post(conn: sqlite3.Connection, idempotency_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, status FROM published_posts WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()


def _record_published_post(
    conn: sqlite3.Connection,
    content_id: int,
    platform_id: int,
    idempotency_key: str,
    status: str,
    external_post_id: str | None = None,
    scheduled_at: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO published_posts
            (content_id, platform_id, idempotency_key, external_post_id,
             status, scheduled_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO UPDATE SET
            status = excluded.status,
            external_post_id = excluded.external_post_id,
            scheduled_at = excluded.scheduled_at,
            error = excluded.error
        """,
        (content_id, platform_id, idempotency_key, external_post_id, status, scheduled_at, error),
    )
    conn.commit()


def publish_batch(
    publisher: Publisher,
    media_host: MediaHost,
    conn: sqlite3.Connection,
    config: Config,
    count: int | None = None,
) -> list[PublishOutcome]:
    platforms = load_active_platforms(conn)
    if not platforms:
        raise PublishingSetupError(
            "No active platforms are configured yet. `ghostcash publish` syncs them "
            "automatically from Buffer on each run - check that your Buffer account "
            "has at least one connected YouTube or Instagram channel."
        )

    requested_count = count if count is not None else config.content.content_per_run
    if requested_count < 1:
        return []

    items = select_ready_content(conn, requested_count)
    if not items:
        return []

    save_to_draft = config.human_approval_mode.publishing.strip().upper() != "AUTONOMOUS"
    outcomes: list[PublishOutcome] = []

    for content in items:
        assert content.id is not None
        assert content.rendered_video_path is not None

        try:
            video_url = media_host.upload(
                content.rendered_video_path, remote_name=f"content_{content.id}.mp4"
            )
        except MediaHostError as exc:
            for platform in platforms:
                outcomes.append(PublishOutcome(content, platform, None, str(exc)))
            continue

        any_success = False
        for platform in platforms:
            assert platform.id is not None
            idempotency_key = _idempotency_key(content.id, platform.name)
            existing = _existing_published_post(conn, idempotency_key)
            if existing is not None and existing["status"] in ("DRAFT", "SCHEDULED", "PUBLISHED"):
                outcomes.append(
                    PublishOutcome(
                        content,
                        platform,
                        ScheduleResult(
                            external_post_id="(already published)", status=existing["status"]
                        ),
                        None,
                    )
                )
                any_success = True
                continue

            try:
                result = publisher.schedule(
                    content, platform, video_url=video_url, save_to_draft=save_to_draft
                )
            except PublisherError as exc:
                _record_published_post(
                    conn, content.id, platform.id, idempotency_key, status="FAILED", error=str(exc)
                )
                outcomes.append(PublishOutcome(content, platform, None, str(exc)))
                continue

            _record_published_post(
                conn,
                content.id,
                platform.id,
                idempotency_key,
                status=result.status,
                external_post_id=result.external_post_id,
                scheduled_at=result.scheduled_at,
            )
            outcomes.append(PublishOutcome(content, platform, result, None))
            any_success = True

        if any_success:
            conn.execute("UPDATE content SET status = 'SCHEDULED' WHERE id = ?", (content.id,))
            conn.commit()

    return outcomes
