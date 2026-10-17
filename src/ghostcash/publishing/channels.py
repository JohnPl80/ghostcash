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

from ghostcash.config.schema import Config
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import SERVICE_TO_PLATFORM, BufferPublisher


@dataclass
class SyncResult:
    synced: list[str]
    skipped_services: list[str]


def resolve_organization_id(publisher: BufferPublisher, config: Config) -> str:
    """The Buffer organization to act on - `publishing.organization_id`
    if set, otherwise the account's only organization. Raises
    PublisherError if the account has none or more than one (most
    Buffer accounts have exactly one, so this is rarely hit in
    practice). Shared by `ghostcash publish` and `ghostcash run`, which
    both need to sync channels before scheduling anything."""
    if config.publishing.organization_id:
        return config.publishing.organization_id

    organizations = publisher.get_organizations()
    if len(organizations) == 1:
        return str(organizations[0]["id"])
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
