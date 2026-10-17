from __future__ import annotations

import pytest

from ghostcash.config.schema import Config
from ghostcash.content.topics import TopicGenerationError, generate_topics
from tests.conftest import FakeAIProvider, sample_raw_topic

CATEGORIES_YAML = "categories:\n  - key: AI\n    label: AI\n  - key: EXCEL\n    label: Excel\n"


@pytest.fixture()
def categories_path(tmp_path):
    path = tmp_path / "content_categories.yaml"
    path.write_text(CATEGORIES_YAML, encoding="utf-8")
    return path


def test_generate_topics_inserts_valid_topics(migrated_conn, categories_path):
    provider = FakeAIProvider(
        {"topics": [sample_raw_topic(), sample_raw_topic(topic="Second idea")]}
    )
    config = Config()

    topics = generate_topics(
        provider, migrated_conn, config, count=2, categories_path=categories_path
    )

    assert len(topics) == 2
    assert all(t.id is not None for t in topics)
    row_count = migrated_conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    assert row_count == 2


def test_generate_topics_computes_overall_score(migrated_conn, categories_path):
    provider = FakeAIProvider({"topics": [sample_raw_topic()]})
    config = Config()

    [topic] = generate_topics(provider, migrated_conn, config, categories_path=categories_path)

    assert topic.overall_score is not None
    assert topic.overall_score > 0


def test_generate_topics_skips_malformed_entries(migrated_conn, categories_path):
    good = sample_raw_topic()
    bad = {"topic": "missing fields"}  # no scores at all
    provider = FakeAIProvider({"topics": [good, bad]})
    config = Config()

    topics = generate_topics(provider, migrated_conn, config, categories_path=categories_path)

    assert len(topics) == 1
    assert topics[0].topic == good["topic"]


def test_generate_topics_raises_when_all_entries_malformed(migrated_conn, categories_path):
    provider = FakeAIProvider({"topics": [{"topic": "no scores"}]})
    config = Config()

    with pytest.raises(TopicGenerationError):
        generate_topics(provider, migrated_conn, config, categories_path=categories_path)


def test_generate_topics_raises_when_response_has_no_topics(migrated_conn, categories_path):
    provider = FakeAIProvider({"topics": []})
    config = Config()

    with pytest.raises(TopicGenerationError):
        generate_topics(provider, migrated_conn, config, categories_path=categories_path)


def test_generate_topics_raises_when_max_requests_per_run_is_zero(migrated_conn, categories_path):
    provider = FakeAIProvider({"topics": [sample_raw_topic()]})
    config = Config()
    config.ai.max_requests_per_run = 0

    with pytest.raises(TopicGenerationError, match="AI_MAX_REQUESTS_PER_RUN"):
        generate_topics(provider, migrated_conn, config, categories_path=categories_path)


def test_generate_topics_raises_when_categories_file_missing(migrated_conn, tmp_path):
    provider = FakeAIProvider({"topics": [sample_raw_topic()]})
    config = Config()

    with pytest.raises(TopicGenerationError):
        generate_topics(
            provider, migrated_conn, config, categories_path=tmp_path / "does-not-exist.yaml"
        )


def test_time_sensitive_topic_without_expiry_is_skipped_not_crashed(migrated_conn, categories_path):
    # Topic.__post_init__ requires expires_at for TIME_SENSITIVE topics;
    # generate_topics should treat that as a malformed entry, not crash.
    bad = sample_raw_topic(freshness="TIME_SENSITIVE", expires_at=None)
    good = sample_raw_topic()
    provider = FakeAIProvider({"topics": [bad, good]})
    config = Config()

    topics = generate_topics(provider, migrated_conn, config, categories_path=categories_path)

    assert len(topics) == 1
    assert topics[0].freshness == "EVERGREEN"


def test_prompt_mentions_requested_count_and_categories(migrated_conn, categories_path):
    provider = FakeAIProvider({"topics": [sample_raw_topic()]})
    config = Config()

    generate_topics(provider, migrated_conn, config, count=7, categories_path=categories_path)

    prompt, schema = provider.structured_calls[0]
    assert "7" in prompt
    assert "AI" in prompt and "EXCEL" in prompt
    assert schema["properties"]["topics"]["items"]["properties"]["category"]["enum"] == [
        "AI",
        "EXCEL",
    ]
