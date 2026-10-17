from __future__ import annotations

import json

import pytest

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.config.schema import Config
from ghostcash.content.generation import (
    ContentGenerationError,
    generate_draft_for_topic,
    generate_scripts,
    select_candidate_topics,
)
from ghostcash.models.topic import Topic
from tests.conftest import FakeAIProvider, sample_content_draft


class _BrokenProvider(AIProvider):
    def generate(self, prompt: str, **kwargs) -> str:
        raise AIProviderError("boom")

    def structured_generate(self, prompt: str, schema: dict) -> dict:
        raise AIProviderError("boom")

    def check_connectivity(self) -> str:
        raise AIProviderError("boom")


def _insert_topic(conn, **overrides) -> int:
    defaults = dict(
        topic="Excel shortcuts", category="EXCEL", overall_score=0.5, status="CANDIDATE"
    )
    defaults.update(overrides)
    cursor = conn.execute(
        "INSERT INTO topics (topic, category, overall_score, status) VALUES (?, ?, ?, ?)",
        (defaults["topic"], defaults["category"], defaults["overall_score"], defaults["status"]),
    )
    conn.commit()
    return cursor.lastrowid


def _topic(**overrides) -> Topic:
    defaults = dict(topic="Excel shortcuts", category="EXCEL", id=1)
    defaults.update(overrides)
    return Topic(**defaults)


def test_generate_draft_for_topic_parses_response(migrated_conn):
    provider = FakeAIProvider(content_response=sample_content_draft())
    draft = generate_draft_for_topic(provider, _topic(), Config(), migrated_conn)
    assert draft.hook
    assert draft.script
    assert draft.content_type in ("INFORMATION", "LIST", "PROBLEM_SOLUTION")
    assert isinstance(draft.visual_sequence, list)


def test_generate_draft_for_topic_raises_on_malformed_response(migrated_conn):
    provider = FakeAIProvider(content_response={"hook": "only a hook"})
    with pytest.raises(ContentGenerationError):
        generate_draft_for_topic(provider, _topic(), Config(), migrated_conn)


def test_select_candidate_topics_orders_by_score_desc(migrated_conn):
    _insert_topic(migrated_conn, topic="low", overall_score=0.1)
    _insert_topic(migrated_conn, topic="high", overall_score=0.9)
    _insert_topic(migrated_conn, topic="mid", overall_score=0.5)

    topics = select_candidate_topics(migrated_conn, limit=10)

    assert [t.topic for t in topics] == ["high", "mid", "low"]


def test_select_candidate_topics_excludes_non_candidate_status(migrated_conn):
    _insert_topic(migrated_conn, topic="used", status="USED", overall_score=0.9)
    _insert_topic(migrated_conn, topic="candidate", status="CANDIDATE", overall_score=0.1)

    topics = select_candidate_topics(migrated_conn, limit=10)

    assert [t.topic for t in topics] == ["candidate"]


def test_select_candidate_topics_respects_limit(migrated_conn):
    for i in range(5):
        _insert_topic(migrated_conn, topic=f"topic {i}", overall_score=float(i))
    topics = select_candidate_topics(migrated_conn, limit=2)
    assert len(topics) == 2


def test_generate_scripts_returns_empty_list_when_no_candidates(migrated_conn):
    provider = FakeAIProvider(content_response=sample_content_draft())
    result = generate_scripts(provider, migrated_conn, Config())
    assert result == []
    # No candidates means no AI call should have been made at all.
    assert provider.structured_calls == []


def test_generate_scripts_inserts_approved_content(migrated_conn):
    _insert_topic(migrated_conn)
    provider = FakeAIProvider(content_response=sample_content_draft())

    results = generate_scripts(provider, migrated_conn, Config(), count=1)

    assert len(results) == 1
    assert results[0].status == "APPROVED"
    assert results[0].id is not None
    row = migrated_conn.execute("SELECT * FROM content WHERE id = ?", (results[0].id,)).fetchone()
    assert row["status"] == "APPROVED"
    assert json.loads(row["visual_sequence_json"]) == sample_content_draft()["visual_sequence"]
    assert (
        row["suggested_product_description"]
        == sample_content_draft()["suggested_product_description"]
    )


def test_generate_scripts_inserts_rejected_content_for_bad_script(migrated_conn):
    _insert_topic(migrated_conn)
    provider = FakeAIProvider(content_response=sample_content_draft(script=""))

    results = generate_scripts(provider, migrated_conn, Config(), count=1)

    assert len(results) == 1
    assert results[0].status == "REJECTED"
    assert results[0].quality_score == 0


def test_generate_scripts_marks_topic_used_on_success(migrated_conn):
    topic_id = _insert_topic(migrated_conn)
    provider = FakeAIProvider(content_response=sample_content_draft())

    generate_scripts(provider, migrated_conn, Config(), count=1)

    status = migrated_conn.execute(
        "SELECT status FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()[0]
    assert status == "USED"


def test_generate_scripts_leaves_topic_candidate_on_ai_failure(migrated_conn):
    topic_id = _insert_topic(migrated_conn)

    with pytest.raises(ContentGenerationError):
        generate_scripts(_BrokenProvider(), migrated_conn, Config(), count=1)

    status = migrated_conn.execute(
        "SELECT status FROM topics WHERE id = ?", (topic_id,)
    ).fetchone()[0]
    assert status == "CANDIDATE"  # left alone so the next run retries it


def test_generate_scripts_stores_alternative_hook_as_variant(migrated_conn):
    _insert_topic(migrated_conn)
    provider = FakeAIProvider(
        content_response=sample_content_draft(alternative_hook="A different angle")
    )

    [content] = generate_scripts(provider, migrated_conn, Config(), count=1)

    variant = migrated_conn.execute(
        "SELECT variant_type, variant_text, status FROM content_variants "
        "WHERE parent_content_id = ?",
        (content.id,),
    ).fetchone()
    assert variant["variant_type"] == "HOOK"
    assert variant["variant_text"] == "A different angle"
    assert variant["status"] == "PROPOSED"


def test_generate_scripts_records_usage_via_tracker(migrated_conn):
    _insert_topic(migrated_conn)
    provider = FakeAIProvider(content_response=sample_content_draft())
    tracker = UsageTracker(migrated_conn)

    generate_scripts(provider, migrated_conn, Config(), count=1, usage_tracker=tracker)

    assert tracker.calls_this_month() == 1


def test_generate_scripts_raises_when_max_requests_per_run_is_zero(migrated_conn):
    _insert_topic(migrated_conn)
    provider = FakeAIProvider(content_response=sample_content_draft())
    config = Config()
    config.ai.max_requests_per_run = 0

    with pytest.raises(ContentGenerationError, match="AI_MAX_REQUESTS_PER_RUN"):
        generate_scripts(provider, migrated_conn, config, count=1)


def test_generate_scripts_zero_count_returns_empty_without_calling_ai(migrated_conn):
    _insert_topic(migrated_conn)
    provider = FakeAIProvider(content_response=sample_content_draft())
    result = generate_scripts(provider, migrated_conn, Config(), count=0)
    assert result == []
    assert provider.structured_calls == []
