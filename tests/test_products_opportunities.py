from __future__ import annotations

import pytest

from ghostcash.config.schema import Config
from ghostcash.products.opportunities import (
    OpportunityDetectionError,
    approve_opportunity,
    detect_opportunities,
    get_opportunity,
    list_opportunities,
    reject_opportunity,
)
from tests.conftest import FakeAIProvider

_PROPOSAL = {
    "proposed_name": "Excel Automation Starter Kit",
    "problem_solved": "Office workers waste hours on manual Excel tasks",
    "contents_outline": ["10 macro templates", "Cheat sheet PDF"],
    "price_recommendation": 19.0,
    "target_customer": "Office workers who use Excel daily",
    "sales_page_copy": "Stop wasting hours. Automate your Excel work today.",
}


def _seed_topic_with_performance(
    conn, views=70000, likes=3000, comments=500, shares=500, category="EXCEL", product_id=None
) -> int:
    conn.execute(
        "INSERT INTO topics (topic, category, target_audience) "
        "VALUES ('Excel automation', ?, 'office workers')",
        (category,),
    )
    topic_id = conn.execute("SELECT id FROM topics").fetchone()["id"]
    conn.execute(
        "INSERT INTO content (topic_id, topic, category, title, status, product_id) "
        "VALUES (?, 'Excel automation', ?, 'title', 'PUBLISHED', ?)",
        (topic_id, category, product_id),
    )
    content_id = conn.execute("SELECT id FROM content WHERE topic_id = ?", (topic_id,)).fetchone()[
        "id"
    ]
    existing_platform = conn.execute("SELECT id FROM platforms WHERE name = 'YOUTUBE'").fetchone()
    if existing_platform:
        platform_id = existing_platform["id"]
    else:
        platform_id = conn.execute(
            "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
        ).lastrowid
    conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes, comments, shares) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (content_id, platform_id, views, likes, comments, shares),
    )
    conn.commit()
    return topic_id


def _config() -> Config:
    return Config()


# -- detect_opportunities -----------------------------------------------------


def test_detect_opportunities_proposes_product_for_qualifying_topic(migrated_conn):
    _seed_topic_with_performance(migrated_conn)
    ai = FakeAIProvider(content_response=_PROPOSAL)

    opportunities = detect_opportunities(ai, migrated_conn, _config())

    assert len(opportunities) == 1
    opp = opportunities[0]
    assert opp.proposed_name == "Excel Automation Starter Kit"
    assert opp.supporting_views == 70000
    assert opp.supporting_clicks is None  # never available - see metrics.py
    assert opp.supporting_engagement_rate == pytest.approx(4000 / 70000 * 100)
    assert opp.status == "PENDING_APPROVAL"


def test_detect_opportunities_skips_topics_below_view_threshold(migrated_conn):
    _seed_topic_with_performance(migrated_conn, views=100)  # below default 1000
    ai = FakeAIProvider(content_response=_PROPOSAL)

    opportunities = detect_opportunities(ai, migrated_conn, _config())

    assert opportunities == []


def test_detect_opportunities_skips_topics_below_engagement_threshold(migrated_conn):
    # High views, but engagement_rate = 10/70000*100 well below the 5% default
    _seed_topic_with_performance(migrated_conn, likes=10, comments=0, shares=0)
    ai = FakeAIProvider(content_response=_PROPOSAL)

    opportunities = detect_opportunities(ai, migrated_conn, _config())

    assert opportunities == []


def test_detect_opportunities_skips_topics_with_existing_product(migrated_conn):
    migrated_conn.execute("INSERT INTO products (name) VALUES ('Existing product')")
    product_id = migrated_conn.execute("SELECT id FROM products").fetchone()["id"]
    _seed_topic_with_performance(migrated_conn, product_id=product_id)
    ai = FakeAIProvider(content_response=_PROPOSAL)

    opportunities = detect_opportunities(ai, migrated_conn, _config())

    assert opportunities == []


def test_detect_opportunities_is_idempotent_across_runs(migrated_conn):
    _seed_topic_with_performance(migrated_conn)
    ai = FakeAIProvider(content_response=_PROPOSAL)

    first = detect_opportunities(ai, migrated_conn, _config())
    second = detect_opportunities(ai, migrated_conn, _config())

    assert len(first) == 1
    assert second == []  # already has a PENDING_APPROVAL opportunity for that topic


def test_detect_opportunities_respects_limit(migrated_conn):
    _seed_topic_with_performance(migrated_conn, category="EXCEL")
    _seed_topic_with_performance(migrated_conn, category="AI")
    ai = FakeAIProvider(content_response=_PROPOSAL)

    opportunities = detect_opportunities(ai, migrated_conn, _config(), limit=1)

    assert len(opportunities) == 1


def test_detect_opportunities_returns_empty_without_error_when_nothing_qualifies(migrated_conn):
    ai = FakeAIProvider(content_response=_PROPOSAL)
    opportunities = detect_opportunities(ai, migrated_conn, _config())
    assert opportunities == []


def test_detect_opportunities_raises_when_ai_fails_on_every_candidate(migrated_conn):
    from ghostcash.ai.base import AIProvider, AIProviderError

    class _BrokenProvider(AIProvider):
        def generate(self, prompt, **kwargs):
            raise AIProviderError("boom")

        def structured_generate(self, prompt, schema):
            raise AIProviderError("boom")

        def check_connectivity(self):
            raise AIProviderError("boom")

    _seed_topic_with_performance(migrated_conn)

    with pytest.raises(OpportunityDetectionError):
        detect_opportunities(_BrokenProvider(), migrated_conn, _config())


# -- approval gate -------------------------------------------------------------


def test_approve_and_reject_opportunity(migrated_conn):
    _seed_topic_with_performance(migrated_conn)
    ai = FakeAIProvider(content_response=_PROPOSAL)
    opp = detect_opportunities(ai, migrated_conn, _config())[0]

    approved = approve_opportunity(migrated_conn, opp.id)
    assert approved.status == "APPROVED"
    assert get_opportunity(migrated_conn, opp.id).status == "APPROVED"


def test_reject_opportunity(migrated_conn):
    _seed_topic_with_performance(migrated_conn)
    ai = FakeAIProvider(content_response=_PROPOSAL)
    opp = detect_opportunities(ai, migrated_conn, _config())[0]

    rejected = reject_opportunity(migrated_conn, opp.id)
    assert rejected.status == "REJECTED"


def test_approve_unknown_opportunity_raises(migrated_conn):
    with pytest.raises(OpportunityDetectionError):
        approve_opportunity(migrated_conn, 999)


def test_approve_already_approved_opportunity_raises(migrated_conn):
    _seed_topic_with_performance(migrated_conn)
    ai = FakeAIProvider(content_response=_PROPOSAL)
    opp = detect_opportunities(ai, migrated_conn, _config())[0]
    approve_opportunity(migrated_conn, opp.id)

    with pytest.raises(OpportunityDetectionError):
        approve_opportunity(migrated_conn, opp.id)


def test_list_opportunities_filters_by_status(migrated_conn):
    _seed_topic_with_performance(migrated_conn, category="EXCEL")
    ai = FakeAIProvider(content_response=_PROPOSAL)
    opp = detect_opportunities(ai, migrated_conn, _config())[0]
    approve_opportunity(migrated_conn, opp.id)

    pending = list_opportunities(migrated_conn, status="PENDING_APPROVAL")
    approved = list_opportunities(migrated_conn, status="APPROVED")

    assert pending == []
    assert len(approved) == 1


def test_list_opportunities_no_filter_returns_all(migrated_conn):
    _seed_topic_with_performance(migrated_conn)
    ai = FakeAIProvider(content_response=_PROPOSAL)
    detect_opportunities(ai, migrated_conn, _config())

    assert len(list_opportunities(migrated_conn, status=None)) == 1
