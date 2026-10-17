"""Tests for ghostcash.health.check_system() (CLAUDE.md section 36).

CLI-level wiring (that `ghostcash health` calls check_system() and
prints its notes/issues, exit code 0/1) is covered in tests/test_cli.py;
this file covers check_system()'s own branches directly - in
particular the Phase 10 additions: live AI/Buffer/Gumroad connectivity
checks (mocked - never real network calls, per CLAUDE.md section 44)
and the last-successful-job-recency check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import ghostcash.health as health_module
from ghostcash.ai.base import AIProviderError
from ghostcash.config.schema import Config
from ghostcash.database.connection import get_connection
from ghostcash.database.jobs import fail_job, start_job, succeed_job
from ghostcash.database.migrate import apply_migrations
from ghostcash.health import check_system
from ghostcash.products.base import StoreError
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher


def _migrated_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    conn = get_connection(path)
    apply_migrations(conn)
    conn.close()
    return path


# -- AI connectivity ---------------------------------------------------------


def test_ai_connectivity_ok_when_key_present_and_reachable(tmp_path, monkeypatch):
    path = _migrated_db_path(tmp_path)
    config = Config()
    config.ai.api_key = "fake-key"

    fake_provider = MagicMock()
    fake_provider.check_connectivity.return_value = "model 'gemini-3.8-flash' is reachable"
    monkeypatch.setattr(health_module, "get_ai_provider", lambda ai_config: fake_provider)

    result = check_system(config, str(path))

    assert result.healthy
    assert any("AI API" in note and "reachable" in note for note in result.notes)


def test_ai_connectivity_issue_when_key_present_but_unreachable(tmp_path, monkeypatch):
    path = _migrated_db_path(tmp_path)
    config = Config()
    config.ai.api_key = "fake-key"

    fake_provider = MagicMock()
    fake_provider.check_connectivity.side_effect = AIProviderError("401 invalid API key")
    monkeypatch.setattr(health_module, "get_ai_provider", lambda ai_config: fake_provider)

    result = check_system(config, str(path))

    assert not result.healthy
    assert any("AI API" in issue and "401 invalid API key" in issue for issue in result.issues)


def test_ai_connectivity_skipped_when_no_key(tmp_path):
    path = _migrated_db_path(tmp_path)
    config = Config()
    assert config.ai.api_key == ""

    result = check_system(config, str(path))

    assert any("AI_API_KEY not set" in note for note in result.notes)


# -- Buffer connectivity ------------------------------------------------------


def test_buffer_connectivity_ok(tmp_path, monkeypatch):
    path = _migrated_db_path(tmp_path)
    config = Config()
    config.buffer_api_key = "fake-key"

    fake_publisher = MagicMock(spec=BufferPublisher)
    fake_publisher.get_account.return_value = {"id": "acc_1", "name": "Test Org"}
    monkeypatch.setattr(health_module, "get_publisher", lambda cfg: fake_publisher)

    result = check_system(config, str(path))

    assert result.healthy
    assert any("Buffer API reachable" in note for note in result.notes)


def test_buffer_connectivity_issue_on_failure(tmp_path, monkeypatch):
    path = _migrated_db_path(tmp_path)
    config = Config()
    config.buffer_api_key = "fake-key"

    fake_publisher = MagicMock(spec=BufferPublisher)
    fake_publisher.get_account.side_effect = PublisherError("401 unauthorized")
    monkeypatch.setattr(health_module, "get_publisher", lambda cfg: fake_publisher)

    result = check_system(config, str(path))

    assert not result.healthy
    assert any("Buffer API not reachable" in issue for issue in result.issues)


def test_buffer_connectivity_skipped_when_no_key(tmp_path):
    path = _migrated_db_path(tmp_path)
    config = Config()
    assert config.buffer_api_key == ""

    result = check_system(config, str(path))

    assert any("BUFFER_API_KEY not set" in note for note in result.notes)


# -- Gumroad connectivity ------------------------------------------------------


def test_gumroad_connectivity_ok(tmp_path, monkeypatch):
    path = _migrated_db_path(tmp_path)
    config = Config()
    config.gumroad_api_key = "fake-key"

    fake_store = MagicMock()
    fake_store.get_sales.return_value = None
    monkeypatch.setattr(health_module, "get_store", lambda cfg: fake_store)

    result = check_system(config, str(path))

    assert result.healthy
    assert any("Gumroad API reachable" in note for note in result.notes)


def test_gumroad_connectivity_issue_on_failure(tmp_path, monkeypatch):
    path = _migrated_db_path(tmp_path)
    config = Config()
    config.gumroad_api_key = "fake-key"

    fake_store = MagicMock()
    fake_store.get_sales.side_effect = StoreError("invalid API key")
    monkeypatch.setattr(health_module, "get_store", lambda cfg: fake_store)

    result = check_system(config, str(path))

    assert not result.healthy
    assert any("Gumroad API not reachable" in issue for issue in result.issues)


# -- last-successful-job recency -----------------------------------------------


def test_job_recency_ok_for_recent_success(tmp_path):
    path = _migrated_db_path(tmp_path)
    conn = get_connection(path)
    job_id = start_job(conn, "nightly_pipeline")
    succeed_job(conn, job_id)
    conn.close()

    result = check_system(Config(), str(path))

    assert result.healthy
    assert any("last successful job" in note for note in result.notes)


def test_job_recency_issue_for_stale_success(tmp_path):
    path = _migrated_db_path(tmp_path)
    conn = get_connection(path)
    job_id = start_job(conn, "nightly_pipeline")
    succeed_job(conn, job_id)
    stale = (datetime.now(UTC) - timedelta(hours=100)).isoformat()
    conn.execute("UPDATE jobs SET finished_at = ? WHERE id = ?", (stale, job_id))
    conn.commit()
    conn.close()

    result = check_system(Config(), str(path))

    assert not result.healthy
    assert any("older than" in issue for issue in result.issues)


def test_job_recency_issue_when_every_job_has_failed(tmp_path):
    path = _migrated_db_path(tmp_path)
    conn = get_connection(path)
    job_id = start_job(conn, "nightly_pipeline")
    fail_job(conn, job_id, "boom")
    conn.close()

    result = check_system(Config(), str(path))

    assert not result.healthy
    assert any("no job has ever completed successfully" in issue for issue in result.issues)


def test_job_recency_not_an_issue_when_no_jobs_have_run(tmp_path):
    path = _migrated_db_path(tmp_path)

    result = check_system(Config(), str(path))

    assert any("no jobs have run yet" in note for note in result.notes)
    assert not any("job" in issue for issue in result.issues)
