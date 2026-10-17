from __future__ import annotations

from unittest.mock import MagicMock

from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.channels import sync_channels


def _publisher_with_channels(channels: list[dict]) -> BufferPublisher:
    publisher = MagicMock(spec=BufferPublisher)
    publisher.get_channels.return_value = channels
    return publisher


def test_sync_channels_upserts_mapped_services(migrated_conn):
    publisher = _publisher_with_channels(
        [
            {
                "id": "ch1",
                "service": "youtube",
                "displayName": "My YT",
                "isLocked": False,
                "isDisconnected": False,
            },
            {
                "id": "ch2",
                "service": "instagram",
                "displayName": "My IG",
                "isLocked": False,
                "isDisconnected": False,
            },
        ]
    )

    result = sync_channels(publisher, migrated_conn, "org1")

    assert sorted(result.synced) == ["INSTAGRAM", "YOUTUBE"]
    rows = migrated_conn.execute(
        "SELECT name, buffer_channel_id, is_active FROM platforms"
    ).fetchall()
    by_name = {r["name"]: r for r in rows}
    assert by_name["YOUTUBE"]["buffer_channel_id"] == "ch1"
    assert by_name["INSTAGRAM"]["buffer_channel_id"] == "ch2"
    assert by_name["YOUTUBE"]["is_active"] == 1


def test_sync_channels_skips_unsupported_services(migrated_conn):
    publisher = _publisher_with_channels(
        [
            {"id": "ch1", "service": "linkedin", "displayName": "LI"},
            {"id": "ch2", "service": "twitter", "displayName": "X"},
        ]
    )

    result = sync_channels(publisher, migrated_conn, "org1")

    assert result.synced == []
    assert sorted(result.skipped_services) == ["linkedin", "twitter"]
    count = migrated_conn.execute("SELECT COUNT(*) FROM platforms").fetchone()[0]
    assert count == 0


def test_sync_channels_still_syncs_tiktok_for_visibility(migrated_conn):
    # TikTok can be connected/synced even though Buffer can't post to it -
    # BufferPublisher.schedule() is what rejects it, not the sync step.
    publisher = _publisher_with_channels(
        [{"id": "ch1", "service": "tiktok", "displayName": "TikTok"}]
    )
    result = sync_channels(publisher, migrated_conn, "org1")
    assert result.synced == ["TIKTOK"]


def test_sync_channels_marks_disconnected_channel_inactive(migrated_conn):
    publisher = _publisher_with_channels(
        [{"id": "ch1", "service": "youtube", "isDisconnected": True, "isLocked": False}]
    )
    sync_channels(publisher, migrated_conn, "org1")
    row = migrated_conn.execute("SELECT is_active FROM platforms WHERE name = 'YOUTUBE'").fetchone()
    assert row["is_active"] == 0


def test_sync_channels_updates_existing_platform_on_rerun(migrated_conn):
    publisher = _publisher_with_channels(
        [{"id": "ch1", "service": "youtube", "isLocked": False, "isDisconnected": False}]
    )
    sync_channels(publisher, migrated_conn, "org1")

    publisher2 = _publisher_with_channels(
        [{"id": "ch1-new-id", "service": "youtube", "isLocked": False, "isDisconnected": False}]
    )
    sync_channels(publisher2, migrated_conn, "org1")

    rows = migrated_conn.execute(
        "SELECT buffer_channel_id FROM platforms WHERE name = 'YOUTUBE'"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["buffer_channel_id"] == "ch1-new-id"
