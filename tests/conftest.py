from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from ghostcash.ai.base import AIProvider
from ghostcash.database.connection import get_connection
from ghostcash.database.migrate import apply_migrations

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeAIProvider(AIProvider):
    """Test double standing in for a real AIProvider - no network calls.

    CLAUDE.md section 44 requires external APIs to be mocked in unit
    tests; this is the shared mock used across the AI/content tests.

    Dispatches based on the requested schema's shape (a topics-batch
    schema has a top-level "topics" array property; a content-draft
    schema doesn't) so a single instance can stand in for both
    generate_topics() and generate_scripts() calls within one test.
    """

    def __init__(
        self,
        topics_response: dict[str, Any] | None = None,
        content_response: dict[str, Any] | None = None,
    ) -> None:
        self.topics_response = topics_response
        self.content_response = content_response
        self.generate_calls: list[str] = []
        self.structured_calls: list[tuple[str, dict[str, Any]]] = []
        self.last_usage: dict[str, int] | None = {"prompt_tokens": 10, "output_tokens": 20}

    def generate(self, prompt: str, **kwargs: Any) -> str:
        self.generate_calls.append(prompt)
        return "fake response"

    def structured_generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.structured_calls.append((prompt, schema))
        is_topics_schema = "topics" in schema.get("properties", {})
        if is_topics_schema:
            return self.topics_response if self.topics_response is not None else {}
        return self.content_response if self.content_response is not None else {}

    def check_connectivity(self) -> str:
        return "fake provider always reachable"


def sample_raw_topic(**overrides: Any) -> dict[str, Any]:
    """A single well-formed raw topic dict, as the AI provider would return it."""
    topic = {
        "topic": "5 Excel shortcuts nobody uses",
        "category": "EXCEL",
        "hook": "You are wasting hours in Excel and don't know it.",
        "target_audience": "Office workers who use Excel daily",
        "freshness": "EVERGREEN",
        "expires_at": None,
        "curiosity_score": 0.8,
        "shareability_score": 0.6,
        "commercial_score": 0.5,
        "evergreen_score": 0.9,
        "production_cost_score": 0.9,
        "competition_score": 0.3,
    }
    topic.update(overrides)
    return topic


def sample_content_draft(**overrides: Any) -> dict[str, Any]:
    """A single well-formed raw content-draft dict, as the AI provider
    would return it for generate_scripts()."""
    draft = {
        "hook": "You are wasting hours in Excel every single week.",
        "script": (
            "Most people never learn keyboard shortcuts in Excel. "
            "Pressing Ctrl+Arrow jumps to the edge of your data instantly. "
            "That one habit alone saves hours every month."
        ),
        "caption": "The Excel shortcut nobody teaches you.",
        "cta": "Follow for more Excel tips.",
        "alternative_hook": "Here's the Excel shortcut your coworkers don't know.",
        "content_type": "INFORMATION",
        "visual_sequence": ["Hook text on screen", "Screen recording of the shortcut", "CTA card"],
        "suggested_product_description": "A downloadable Excel shortcuts cheat sheet.",
    }
    draft.update(overrides)
    return draft


@pytest.fixture()
def migrated_conn(tmp_path) -> sqlite3.Connection:
    """A SQLite connection with all migrations applied, on a throwaway file."""
    conn = get_connection(tmp_path / "test.db")
    apply_migrations(conn)
    yield conn
    conn.close()


@pytest.fixture()
def isolated_project(tmp_path, monkeypatch):
    """A throwaway directory that looks like a fresh clone of the repo,
    for testing CLI commands that touch the filesystem (init/health)."""
    project_dir = tmp_path / "project"
    (project_dir / "config").mkdir(parents=True)
    (project_dir / "templates" / "video").mkdir(parents=True)

    (project_dir / "config" / "config.example.yaml").write_text(
        (REPO_ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project_dir / "config" / "content_categories.yaml").write_text(
        (REPO_ROOT / "config" / "content_categories.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (project_dir / ".env.example").write_text(
        (REPO_ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for template_file in (REPO_ROOT / "templates" / "video").glob("*.yaml"):
        (project_dir / "templates" / "video" / template_file.name).write_text(
            template_file.read_text(encoding="utf-8"), encoding="utf-8"
        )

    monkeypatch.chdir(project_dir)
    return project_dir
