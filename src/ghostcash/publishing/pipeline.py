"""Publishing orchestration (CLAUDE.md sections 19-22).

publish_batch() uploads each READY content item's video once (media
host), then attempts a post on every active platform, recording one
published_posts row per (content, platform) pair keyed by a stable
idempotency key - a re-run never double-publishes (section 57), and a
failure on one platform doesn't block the others (section 21).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
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


# Given Buffer channel ids, returns how many posts wait in each channel's
# queue (every id present in the result). See buffer_queue.py.
QueueCounter = Callable[[list[str]], dict[str, int]]

# Notes added by publish_batch: a line starting with HOLD_PREFIX means videos
# were held back on purpose (a channel's queue is full - normal, not a
# failure); one starting with QUEUE_UNREADABLE_PREFIX means Buffer's queue
# could not be read, which callers should treat as a failure.
HOLD_PREFIX = "[hold]"
QUEUE_UNREADABLE_PREFIX = "[error]"


def _note(notes: list[str] | None, message: str) -> None:
    if notes is not None:
        notes.append(message)


def media_asset_name(content_id: int, video_path: str, brand_key: str = "") -> str:
    """The name a video is stored under on the media host.

    The media host reuses any existing file with the same name, so a name
    made only of the content number is dangerous: numbers can repeat (for
    example if the database were ever reset) and a brand-new post would
    silently point at an old, different video. A short fingerprint of the
    file itself makes the name unique to this exact video, while a re-run
    with the same file still finds - and reuses - its earlier upload.
    A brand's videos carry its key in front (`oldwisdom_content_7_...`), since
    every brand numbers its content from 1."""
    prefix = f"{brand_key}_" if brand_key else ""
    digest = hashlib.sha256()
    try:
        with open(video_path, "rb") as video:
            for chunk in iter(lambda: video.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        # Unreadable file: keep the plain name; the media host reports the
        # missing file itself with a clear error.
        return f"{prefix}content_{content_id}.mp4"
    return f"{prefix}content_{content_id}_{digest.hexdigest()[:12]}.mp4"


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
    *,
    queue_counter: QueueCounter | None = None,
    notes: list[str] | None = None,
) -> list[PublishOutcome]:
    """Publish READY content.

    In AUTONOMOUS mode (posts go straight into Buffer's queue) and when a
    `queue_counter` is given, a video is only sent while EVERY active
    channel has room under `publishing.max_scheduled_per_channel`; otherwise
    the run holds back and says why in `notes` (the caller prints them).
    If Buffer's queue cannot be read, nothing is sent (fail closed): a
    wrong guess would leave a video on one channel and not the other."""
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

    room: dict[str, int] | None = None  # Buffer channel id -> free places
    cap = config.publishing.max_scheduled_per_channel
    if not save_to_draft and cap > 0 and queue_counter is not None:
        channel_ids = [str(platform.buffer_channel_id) for platform in platforms]
        try:
            waiting = queue_counter(channel_ids)
        except PublisherError as exc:
            _note(
                notes,
                f"{QUEUE_UNREADABLE_PREFIX} Could not read Buffer's queue, so nothing was sent "
                f"this run: {exc}",
            )
            return outcomes
        room = {cid: cap - waiting.get(cid, 0) for cid in channel_ids}
        _note(
            notes,
            "Buffer queue (waiting / limit): "
            + ", ".join(
                f"{platform.name} {waiting.get(str(platform.buffer_channel_id), 0)}/{cap}"
                for platform in platforms
            ),
        )

    for content in items:
        assert content.id is not None
        assert content.rendered_video_path is not None

        if room is not None:
            full = [
                platform.name
                for platform in platforms
                if room[str(platform.buffer_channel_id)] <= 0
            ]
            if full:
                _note(
                    notes,
                    f"{HOLD_PREFIX} Buffer's queue is full on {', '.join(full)} "
                    f"(limit {cap}); the remaining video(s) wait for the next run.",
                )
                break

        try:
            video_url = media_host.upload(
                content.rendered_video_path,
                remote_name=media_asset_name(
                    content.id, content.rendered_video_path, config.brand.key
                ),
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
            if room is not None:
                room[str(platform.buffer_channel_id)] -= 1

        if any_success:
            conn.execute("UPDATE content SET status = 'SCHEDULED' WHERE id = ?", (content.id,))
            conn.commit()

    return outcomes
