"""Sync Buffer channels into the local `platforms` table (CLAUDE.md
section 21 - "make channel selection configuration-driven").

Only channels whose Buffer `service` maps to a platform GhostCash's
schema models (YOUTUBE/TIKTOK/INSTAGRAM) are synced; other connected
channels (LinkedIn, X, etc.) are outside GhostCash's current scope and
are silently skipped, not errored on - connecting extra channels in
Buffer for other purposes shouldn't break this sync.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ghostcash.config.schema import Config
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import SERVICE_TO_PLATFORM, BufferPublisher


@dataclass
class SyncResult:
    synced: list[str]
    skipped_services: list[str]


# Saved in the `settings` table so routine runs do not spend Buffer API
# requests on facts that rarely change (Buffer's free plan allows 1,000
# requests per channel per 30 days; on 2026-11-13 GhostCash used them up).
ORGANIZATION_SETTING = "buffer.organization_id"
CHANNELS_SYNCED_SETTING = "buffer.channels_synced_at"
CHANNEL_SYNC_INTERVAL = timedelta(hours=20)


def _get_setting(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row and row[0] else None


def _set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, value, datetime.now(UTC).isoformat()),
    )
    conn.commit()


def resolve_organization_id(
    publisher: BufferPublisher, config: Config, conn: sqlite3.Connection | None = None
) -> str:
    """The Buffer organization to act on - `publishing.organization_id`
    if set, otherwise the account's only organization. Raises
    PublisherError if the account has none or more than one (most
    Buffer accounts have exactly one, so this is rarely hit in
    practice). Shared by `ghostcash publish` and `ghostcash run`, which
    both need to sync channels before scheduling anything."""
    if config.publishing.organization_id:
        return config.publishing.organization_id
    if conn is not None:
        cached = _get_setting(conn, ORGANIZATION_SETTING)
        if cached:
            return cached

    organizations = publisher.get_organizations()
    if len(organizations) == 1:
        organization_id = str(organizations[0]["id"])
        if conn is not None:
            _set_setting(conn, ORGANIZATION_SETTING, organization_id)
        return organization_id
    if not organizations:
        raise PublisherError("Your Buffer account has no organizations.")

    listing = ", ".join(f"{org['id']} ({org.get('name', '?')})" for org in organizations)
    raise PublisherError(
        f"Your Buffer account has multiple organizations: {listing}. "
        "Set publishing.organization_id in config.yaml to pick one."
    )


def sync_channels(
    publisher: BufferPublisher, conn: sqlite3.Connection, organization_id: str
) -> SyncResult:
    channels = publisher.get_channels(organization_id)

    synced: list[str] = []
    skipped: list[str] = []

    for channel in channels:
        service = str(channel.get("service", "")).lower()
        platform_name = SERVICE_TO_PLATFORM.get(service)
        if platform_name is None:
            skipped.append(service)
            continue

        config = {
            "buffer_service": service,
            "display_name": channel.get("displayName") or channel.get("name"),
            "is_locked": channel.get("isLocked", False),
            "is_disconnected": channel.get("isDisconnected", False),
        }
        is_active = not channel.get("isDisconnected", False) and not channel.get("isLocked", False)

        conn.execute(
            """
            INSERT INTO platforms (name, buffer_channel_id, is_active, config_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                buffer_channel_id = excluded.buffer_channel_id,
                is_active = excluded.is_active,
                config_json = excluded.config_json
            """,
            (platform_name, channel["id"], int(is_active), json.dumps(config)),
        )
        synced.append(platform_name)

    conn.commit()
    return SyncResult(synced=synced, skipped_services=skipped)


def sync_channels_if_stale(
    publisher: BufferPublisher,
    conn: sqlite3.Connection,
    organization_id: str,
    now: datetime | None = None,
) -> SyncResult | None:
    """`sync_channels`, but at most once per CHANNEL_SYNC_INTERVAL (and
    always when no channel is known yet). Returns None when skipped."""
    now = now or datetime.now(UTC)
    known = conn.execute(
        "SELECT 1 FROM platforms WHERE buffer_channel_id IS NOT NULL LIMIT 1"
    ).fetchone()
    last = _get_setting(conn, CHANNELS_SYNCED_SETTING)
    if known and last:
        try:
            if now - datetime.fromisoformat(last) < CHANNEL_SYNC_INTERVAL:
                return None
        except ValueError:
            pass
    result = sync_channels(publisher, conn, organization_id)
    _set_setting(conn, CHANNELS_SYNCED_SETTING, now.isoformat())
    return result
