from __future__ import annotations

import pytest

from ghostcash.models import Content, Job, Platform, Topic


def test_content_defaults_to_idea_status():
    content = Content(topic="Excel shortcuts", category="EXCEL")
    assert content.status == "IDEA"
    assert content.id is None


def test_content_rejects_invalid_status():
    with pytest.raises(ValueError):
        Content(topic="x", category="AI", status="NOT_REAL")


def test_content_round_trips_through_sqlite_row(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO content (topic, category, title, status) VALUES (?, ?, ?, ?)",
        ("Excel shortcuts", "EXCEL", "5 Excel tricks", "DRAFT"),
    )
    row = migrated_conn.execute("SELECT * FROM content").fetchone()
    content = Content.from_row(row)
    assert content.id is not None
    assert content.topic == "Excel shortcuts"
    assert content.title == "5 Excel tricks"
    assert content.status == "DRAFT"


def test_topic_time_sensitive_requires_expiry():
    with pytest.raises(ValueError):
        Topic(topic="x", category="AI", freshness="TIME_SENSITIVE")

    # Should not raise when expires_at is provided.
    topic = Topic(topic="x", category="AI", freshness="TIME_SENSITIVE", expires_at="2026-01-01")
    assert topic.expires_at == "2026-01-01"


def test_platform_rejects_unknown_name():
    with pytest.raises(ValueError):
        Platform(name="MYSPACE")


def test_platform_round_trips_is_active_as_bool(migrated_conn):
    migrated_conn.execute("INSERT INTO platforms (name, is_active) VALUES ('TIKTOK', 0)")
    row = migrated_conn.execute("SELECT * FROM platforms").fetchone()
    platform = Platform.from_row(row)
    assert platform.is_active is False


def test_job_defaults_and_status_validation():
    job = Job(job_type="generate_topics")
    assert job.status == "PENDING"
    assert job.attempt == 0

    with pytest.raises(ValueError):
        Job(job_type="generate_topics", status="BOGUS")
