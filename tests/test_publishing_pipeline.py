from __future__ import annotations

import pytest

from ghostcash.config.schema import Config
from ghostcash.publishing.base import Publisher, PublisherError, ScheduleResult
from ghostcash.publishing.media_host import MediaHost, MediaHostError
from ghostcash.publishing.pipeline import PublishingSetupError, publish_batch


def _insert_ready_content(conn, video_path="/tmp/video.mp4", **overrides) -> int:
    defaults = dict(topic="t", category="AI", title="title", caption="A caption", status="READY")
    defaults.update(overrides)
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, caption, status, rendered_video_path) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            defaults["topic"],
            defaults["category"],
            defaults["title"],
            defaults["caption"],
            defaults["status"],
            video_path,
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_platform(conn, name="YOUTUBE", buffer_channel_id="ch1", is_active=1) -> int:
    cursor = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id, is_active) VALUES (?, ?, ?)",
        (name, buffer_channel_id, is_active),
    )
    conn.commit()
    return cursor.lastrowid


class _FakeMediaHost(MediaHost):
    def __init__(self, url="https://example.com/video.mp4", error=None):
        self.url = url
        self.error = error
        self.upload_calls: list[tuple[str, str]] = []

    def upload(self, file_path, remote_name):
        self.upload_calls.append((file_path, remote_name))
        if self.error:
            raise self.error
        return self.url


class _FakePublisher(Publisher):
    def __init__(self, result=None, error=None):
        self.result = result or ScheduleResult(external_post_id="post1", status="DRAFT")
        self.error = error
        self.schedule_calls: list[tuple] = []

    def schedule(self, content, platform, *, video_url=None, save_to_draft=False):
        self.schedule_calls.append((content, platform, video_url, save_to_draft))
        if self.error:
            raise self.error
        return self.result


def test_publish_batch_raises_setup_error_without_active_platforms(migrated_conn):
    with pytest.raises(PublishingSetupError):
        publish_batch(_FakePublisher(), _FakeMediaHost(), migrated_conn, Config())


def test_publish_batch_returns_empty_when_nothing_ready(migrated_conn):
    _insert_platform(migrated_conn)
    result = publish_batch(_FakePublisher(), _FakeMediaHost(), migrated_conn, Config())
    assert result == []


def test_publish_batch_creates_published_posts_row(migrated_conn):
    _insert_platform(migrated_conn)
    content_id = _insert_ready_content(migrated_conn)
    publisher = _FakePublisher()
    media_host = _FakeMediaHost()

    outcomes = publish_batch(publisher, media_host, migrated_conn, Config(), count=1)

    assert len(outcomes) == 1
    assert outcomes[0].result.status == "DRAFT"
    row = migrated_conn.execute(
        "SELECT status, external_post_id FROM published_posts WHERE content_id = ?", (content_id,)
    ).fetchone()
    assert row["status"] == "DRAFT"
    assert row["external_post_id"] == "post1"


def test_publish_batch_marks_content_scheduled_on_success(migrated_conn):
    _insert_platform(migrated_conn)
    content_id = _insert_ready_content(migrated_conn)

    publish_batch(_FakePublisher(), _FakeMediaHost(), migrated_conn, Config(), count=1)

    status = migrated_conn.execute(
        "SELECT status FROM content WHERE id = ?", (content_id,)
    ).fetchone()[0]
    assert status == "SCHEDULED"


def test_publish_batch_uses_autonomous_mode_to_schedule_not_draft(migrated_conn):
    _insert_platform(migrated_conn)
    _insert_ready_content(migrated_conn)
    publisher = _FakePublisher(result=ScheduleResult(external_post_id="p1", status="SCHEDULED"))
    config = Config()
    config.human_approval_mode.publishing = "AUTONOMOUS"

    publish_batch(publisher, _FakeMediaHost(), migrated_conn, config, count=1)

    assert publisher.schedule_calls[0][3] is False  # save_to_draft=False


def test_publish_batch_approval_required_uses_draft(migrated_conn):
    _insert_platform(migrated_conn)
    _insert_ready_content(migrated_conn)
    publisher = _FakePublisher()
    config = Config()
    config.human_approval_mode.publishing = "APPROVAL_REQUIRED"

    publish_batch(publisher, _FakeMediaHost(), migrated_conn, config, count=1)

    assert publisher.schedule_calls[0][3] is True  # save_to_draft=True


def test_publish_batch_records_failure_without_aborting(migrated_conn):
    _insert_platform(migrated_conn, name="YOUTUBE", buffer_channel_id="ch1")
    _insert_platform(migrated_conn, name="INSTAGRAM", buffer_channel_id="ch2")
    content_id = _insert_ready_content(migrated_conn)

    class HalfFailingPublisher(Publisher):
        def schedule(self, content, platform, *, video_url=None, save_to_draft=False):
            if platform.name == "YOUTUBE":
                raise PublisherError("boom")
            return ScheduleResult(external_post_id="ok", status="DRAFT")

    outcomes = publish_batch(
        HalfFailingPublisher(), _FakeMediaHost(), migrated_conn, Config(), count=1
    )

    assert len(outcomes) == 2
    failed = [o for o in outcomes if o.error is not None]
    succeeded = [o for o in outcomes if o.result is not None]
    assert len(failed) == 1
    assert len(succeeded) == 1
    # Content still ends up SCHEDULED because at least one platform succeeded.
    status = migrated_conn.execute(
        "SELECT status FROM content WHERE id = ?", (content_id,)
    ).fetchone()[0]
    assert status == "SCHEDULED"


def test_publish_batch_media_host_failure_fails_all_platforms_for_that_content(migrated_conn):
    _insert_platform(migrated_conn)
    _insert_ready_content(migrated_conn)
    media_host = _FakeMediaHost(error=MediaHostError("upload failed"))

    outcomes = publish_batch(_FakePublisher(), media_host, migrated_conn, Config(), count=1)

    assert len(outcomes) == 1
    assert outcomes[0].error == "upload failed"


def test_publish_batch_is_idempotent_on_rerun(migrated_conn):
    _insert_platform(migrated_conn)
    _insert_ready_content(migrated_conn)
    publisher = _FakePublisher()
    media_host = _FakeMediaHost()

    publish_batch(publisher, media_host, migrated_conn, Config(), count=1)
    # Content status is now SCHEDULED, so it won't be selected again by
    # select_ready_content - simulate a re-run picking up nothing new.
    second_run = publish_batch(publisher, media_host, migrated_conn, Config(), count=1)

    assert second_run == []
    # Only one upload/schedule call happened in total.
    assert len(media_host.upload_calls) == 1
    assert len(publisher.schedule_calls) == 1


def test_publish_batch_skips_inactive_platforms(migrated_conn):
    _insert_platform(migrated_conn, name="YOUTUBE", is_active=0)
    with pytest.raises(PublishingSetupError):
        publish_batch(_FakePublisher(), _FakeMediaHost(), migrated_conn, Config())
