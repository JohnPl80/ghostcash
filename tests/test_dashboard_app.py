from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ghostcash.cli.main import main
from ghostcash.config.loader import load_config
from ghostcash.dashboard.app import create_app
from ghostcash.database.connection import get_connection


@pytest.fixture()
def project(isolated_project: Path):
    """A fully initialized project (migrated DB, real config/templates on
    disk) - `ghostcash dashboard`'s own setup, done once per test via the
    CLI itself rather than hand-rolling it differently here."""
    assert main(["init"]) == 0
    return isolated_project


@pytest.fixture()
def client(project: Path) -> TestClient:
    config = load_config(None)
    app = create_app(config, "data/ghostcash.db")
    return TestClient(app)


def _seed_healthy_queue(project: Path) -> None:
    config = load_config(None)
    conn = get_connection("data/ghostcash.db")
    needed = config.queue.emergency_threshold_days * config.publishing.posts_per_day
    for i in range(needed):
        conn.execute(
            "INSERT INTO content (topic, category, status) VALUES (?, 'AI', 'READY')", (f"t{i}",)
        )
    conn.commit()
    conn.close()


def test_all_six_pages_return_200(client: TestClient) -> None:
    for path in ("/", "/content", "/products", "/analytics", "/jobs", "/system"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "GhostCash" in response.text
        assert 'href="/content"' in response.text  # nav renders on every page


def test_overview_shows_attention_banner_on_fresh_empty_database(client: TestClient) -> None:
    response = client.get("/")
    assert "ATTENTION REQUIRED" in response.text
    assert "queue" in response.text.lower()


def test_overview_shows_ok_banner_once_queue_and_approvals_are_clear(
    project: Path, client: TestClient
) -> None:
    _seed_healthy_queue(project)
    response = client.get("/")
    assert "RUNNING - NOTHING REQUIRED" in response.text


def test_overview_lists_pending_product_approval(project: Path, client: TestClient) -> None:
    conn = get_connection("data/ghostcash.db")
    conn.execute(
        "INSERT INTO product_opportunities (proposed_name, status) "
        "VALUES ('Excel Kit', 'PENDING_APPROVAL')"
    )
    conn.commit()
    conn.close()

    response = client.get("/")
    assert "awaiting approval" in response.text


def test_content_page_lists_rows_and_filters_by_status(project: Path, client: TestClient) -> None:
    conn = get_connection("data/ghostcash.db")
    conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('Excel tips', 'EXCEL', 'READY')"
    )
    conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('AI news', 'AI', 'REJECTED')"
    )
    conn.commit()
    conn.close()

    unfiltered = client.get("/content")
    assert "Excel tips" in unfiltered.text
    assert "AI news" in unfiltered.text

    filtered = client.get("/content", params={"status": "READY"})
    assert "Excel tips" in filtered.text
    assert "AI news" not in filtered.text
    assert "Filtered by status" in filtered.text


def test_products_page_lists_opportunities_and_products(project: Path, client: TestClient) -> None:
    conn = get_connection("data/ghostcash.db")
    conn.execute(
        "INSERT INTO product_opportunities (proposed_name, status, price_recommendation) "
        "VALUES ('Notion Kit', 'PENDING_APPROVAL', 15.0)"
    )
    conn.execute(
        "INSERT INTO products (name, status, price, external_product_id, store_provider) "
        "VALUES ('Excel Kit', 'ACTIVE', 19.0, 'excelkit', 'gumroad')"
    )
    conn.commit()
    conn.close()

    response = client.get("/products")
    assert "Notion Kit" in response.text
    assert "Excel Kit" in response.text
    assert "excelkit" in response.text


def test_analytics_page_lists_content_performance(project: Path, client: TestClient) -> None:
    conn = get_connection("data/ghostcash.db")
    conn.execute(
        "INSERT INTO content (topic, category, status) VALUES ('Excel tips', 'EXCEL', 'WINNER')"
    )
    content_id = conn.execute("SELECT id FROM content").fetchone()["id"]
    platform_id = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
    ).lastrowid
    conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, winner_score) "
        "VALUES (?, ?, 5000, 0.81)",
        (content_id, platform_id),
    )
    conn.commit()
    conn.close()

    response = client.get("/analytics")
    assert "Excel tips" in response.text
    assert "0.81" in response.text


def test_jobs_page_lists_recent_jobs(project: Path, client: TestClient) -> None:
    conn = get_connection("data/ghostcash.db")
    conn.execute(
        "INSERT INTO jobs (job_type, status, error) VALUES ('render', 'FAILED', 'disk full')"
    )
    conn.commit()
    conn.close()

    response = client.get("/jobs")
    assert "render" in response.text
    assert "disk full" in response.text


def test_system_page_matches_cli_health_check(project: Path, client: TestClient) -> None:
    response = client.get("/system")
    assert response.status_code == 200
    assert "HEALTHY" in response.text  # isolated_project + init() is a clean install


def test_unmigrated_database_returns_a_friendly_503_not_a_crash(
    isolated_project: Path,
) -> None:
    # No `main(["init"])` here on purpose - the database file doesn't
    # exist/isn't migrated yet. Every route except /system queries real
    # tables, so this must be caught by the app's sqlite3.Error handler
    # rather than raising all the way out as a 500.
    config = load_config(None)
    app = create_app(config, "data/ghostcash.db")
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/")

    assert response.status_code == 503
    assert "ghostcash init" in response.text
