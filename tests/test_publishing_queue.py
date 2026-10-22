from __future__ import annotations

from ghostcash.config.schema import Config
from ghostcash.publishing.queue import get_queue_status


def _insert_content(conn, status, count=1):
    for _ in range(count):
        conn.execute(
            "INSERT INTO content (topic, category, status) VALUES ('t', 'AI', ?)", (status,)
        )
    conn.commit()


def _insert_scheduled_content(conn, post_statuses, count=1):
    """SCHEDULED content, each with one published_posts row per entry in
    `post_statuses` (e.g. ["DRAFT", "DRAFT"] = a draft on two channels)."""
    conn.execute(
        "INSERT OR IGNORE INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'c')"
    )
    platform_id = conn.execute("SELECT id FROM platforms WHERE name = 'YOUTUBE'").fetchone()[0]
    ids = []
    for _ in range(count):
        content_id = conn.execute(
            "INSERT INTO content (topic, category, status) VALUES ('t', 'AI', 'SCHEDULED')"
        ).lastrowid
        for index, post_status in enumerate(post_statuses):
            conn.execute(
                "INSERT INTO published_posts "
                "(content_id, platform_id, idempotency_key, external_post_id, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    content_id,
                    platform_id,
                    f"k{content_id}-{index}",
                    f"p{content_id}-{index}",
                    post_status,
                ),
            )
        ids.append(content_id)
    conn.commit()
    return ids


def test_queue_status_counts_ready_and_scheduled(migrated_conn):
    _insert_content(migrated_conn, "READY", 3)
    _insert_scheduled_content(migrated_conn, ["DRAFT", "DRAFT"], count=2)
    _insert_content(migrated_conn, "DRAFT", 5)  # should not count

    config = Config()
    config.publishing.posts_per_day = 5

    status = get_queue_status(migrated_conn, config)

    assert status.ready_count == 3
    assert status.scheduled_count == 2
    assert status.days_remaining == 1.0  # (3+2)/5


def test_queue_status_thresholds(migrated_conn):
    _insert_content(migrated_conn, "READY", 1)
    config = Config()
    config.publishing.posts_per_day = 5
    config.queue.emergency_threshold_days = 3
    config.queue.alert_threshold_days = 1

    status = get_queue_status(migrated_conn, config)

    assert status.days_remaining == 0.2
    assert status.is_below_emergency_threshold is True
    assert status.is_below_alert_threshold is True


def test_queue_status_healthy_queue_has_no_alerts(migrated_conn):
    _insert_content(migrated_conn, "READY", 20)
    config = Config()
    config.publishing.posts_per_day = 5

    status = get_queue_status(migrated_conn, config)

    assert status.days_remaining == 4.0
    assert status.is_below_emergency_threshold is False
    assert status.is_below_alert_threshold is False


def test_queue_status_guards_against_zero_posts_per_day(migrated_conn):
    _insert_content(migrated_conn, "READY", 5)
    config = Config()
    config.publishing.posts_per_day = 0  # would divide by zero if unguarded

    status = get_queue_status(migrated_conn, config)

    assert status.days_remaining == 5.0  # treated as 1/day minimum


def test_queue_ignores_content_whose_posts_already_went_out(migrated_conn):
    # content.status stays SCHEDULED forever; once every post is PUBLISHED
    # there is nothing left in the queue for it.
    _insert_scheduled_content(migrated_conn, ["PUBLISHED", "PUBLISHED"], count=4)
    _insert_scheduled_content(migrated_conn, ["SCHEDULED", "DRAFT"], count=1)

    status = get_queue_status(migrated_conn, Config())

    assert status.scheduled_count == 1


def test_queue_counts_content_with_one_post_still_waiting(migrated_conn):
    _insert_scheduled_content(migrated_conn, ["PUBLISHED", "DRAFT"], count=1)
    assert get_queue_status(migrated_conn, Config()).scheduled_count == 1


def test_queue_ignores_rejected_and_postless_scheduled_content(migrated_conn):
    _insert_content(migrated_conn, "REJECTED", 3)
    _insert_content(migrated_conn, "SCHEDULED", 2)  # no posts in Buffer at all
    _insert_scheduled_content(migrated_conn, ["FAILED"], count=1)

    assert get_queue_status(migrated_conn, Config()).scheduled_count == 0


def test_queue_counts_approved_content_waiting_for_its_video(migrated_conn):
    """Scripts approved but not yet rendered (the voice service's daily limit
    ran out) will become videos on a later run, so they are runway too -
    otherwise the emergency generator keeps writing scripts that pile up."""
    _insert_content(migrated_conn, "APPROVED", 5)
    _insert_content(migrated_conn, "FAILED", 2)  # permanently broken: not counted
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status, rendered_video_path) "
        "VALUES ('t', 'AI', 'APPROVED', '/tmp/already.mp4')"
    )
    migrated_conn.commit()
    config = Config()
    config.publishing.posts_per_day = 5

    status = get_queue_status(migrated_conn, config)

    assert status.approved_count == 5
    assert status.days_remaining == 1.0
    assert status.waiting_note == ", 5 waiting to be rendered"


def test_queue_waiting_note_is_empty_when_nothing_waits(migrated_conn):
    _insert_content(migrated_conn, "READY", 2)
    status = get_queue_status(migrated_conn, Config())
    assert status.approved_count == 0
    assert status.waiting_note == ""


def test_queue_below_target_drives_the_nightly_generation_gate(migrated_conn):
    config = Config()
    config.publishing.posts_per_day = 2
    config.queue.target_days = 7  # 14 videos

    _insert_content(migrated_conn, "READY", 13)
    assert get_queue_status(migrated_conn, config).is_below_target is True

    _insert_content(migrated_conn, "READY", 1)
    assert get_queue_status(migrated_conn, config).is_below_target is False
