from __future__ import annotations

from ghostcash.ai.budget import UsageTracker
from ghostcash.config.schema import Config
from ghostcash.optimization.loop import run_optimization_cycle
from ghostcash.optimization.weights import get_category_weights
from tests.conftest import FakeAIProvider


def _insert_platform(conn, name="YOUTUBE") -> int:
    cursor = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES (?, ?)", (name, "ch1")
    )
    conn.commit()
    return cursor.lastrowid


def _insert_content(conn, **overrides) -> int:
    defaults = dict(
        topic="t",
        category="EXCEL",
        title="title",
        hook="hook",
        script="script",
        cta="cta",
        content_type="INFORMATION",
        status="PUBLISHED",
    )
    defaults.update(overrides)
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, hook, script, cta, content_type, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(
            defaults[k]
            for k in (
                "topic",
                "category",
                "title",
                "hook",
                "script",
                "cta",
                "content_type",
                "status",
            )
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_performance(conn, content_id, platform_id, winner_score) -> None:
    conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, winner_score, "
        "engagement_rate, revenue_per_1000_views) VALUES (?, ?, 1000, ?, 5.0, 2.0)",
        (content_id, platform_id, winner_score),
    )
    conn.commit()


def _variant_payload() -> dict:
    return {
        "alternative_hooks": ["Alt hook " + str(i) for i in range(5)],
        "alternative_scripts": ["Alt script " + str(i) for i in range(5)],
        "alternative_ctas": ["Alt cta " + str(i) for i in range(3)],
        "related_topics": ["Alt topic " + str(i) for i in range(3)],
    }


def test_run_optimization_cycle_with_empty_database_is_a_safe_no_op(migrated_conn):
    report = run_optimization_cycle(None, migrated_conn, Config())

    assert report.replications == []
    assert report.replication_skipped == "AI_API_KEY not set - skipping winner replication."
    assert report.experiment_evaluations == []
    assert all(weights == {} for weights in report.updated_weights.values())


def test_run_optimization_cycle_updates_weights_from_performance(migrated_conn):
    platform_id = _insert_platform(migrated_conn)
    config = Config()
    config.optimization.min_sample_size = 1

    for i in range(3):
        cid = _insert_content(migrated_conn, category="EXCEL", topic=f"excel-{i}")
        _insert_performance(migrated_conn, cid, platform_id, winner_score=90.0)
    for i in range(3):
        cid = _insert_content(migrated_conn, category="AI", topic=f"ai-{i}")
        _insert_performance(migrated_conn, cid, platform_id, winner_score=10.0)

    report = run_optimization_cycle(None, migrated_conn, config)

    assert report.updated_weights["category"]["EXCEL"] > report.updated_weights["category"]["AI"]
    # Persisted, not just returned.
    persisted = get_category_weights(migrated_conn)
    assert persisted == report.updated_weights["category"]


def test_run_optimization_cycle_replicates_winners_when_ai_provider_given(migrated_conn):
    content_id = _insert_content(migrated_conn, status="WINNER")
    provider = FakeAIProvider(content_response=_variant_payload())
    usage_tracker = UsageTracker(migrated_conn)

    report = run_optimization_cycle(provider, migrated_conn, Config(), usage_tracker)

    assert len(report.replications) == 1
    assert report.replications[0].content_id == content_id
    assert report.replication_skipped is None


def test_run_optimization_cycle_skips_replication_without_ai_provider(migrated_conn):
    _insert_content(migrated_conn, status="WINNER")

    report = run_optimization_cycle(None, migrated_conn, Config())

    assert report.replications == []
    assert "AI_API_KEY not set" in report.replication_skipped


def test_run_optimization_cycle_evaluates_experiments(migrated_conn):
    content_id = _insert_content(migrated_conn, status="WINNER")
    provider = FakeAIProvider(content_response=_variant_payload())
    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 1

    # First cycle replicates the winner and opens an experiment.
    report1 = run_optimization_cycle(provider, migrated_conn, config)
    variant_id = report1.replications[0].variant_content_id

    platform_id = _insert_platform(migrated_conn)
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes, comments, shares) "
        "VALUES (?, ?, 1000, 10, 5, 5)",
        (content_id, platform_id),
    )
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes, comments, shares) "
        "VALUES (?, ?, 1000, 50, 20, 20)",
        (variant_id, platform_id),
    )
    migrated_conn.commit()

    # Second cycle (no more winners due) should evaluate the now-ready
    # experiment.
    report2 = run_optimization_cycle(None, migrated_conn, config)

    assert len(report2.experiment_evaluations) == 1
    assert report2.experiment_evaluations[0].result == "VARIANT_WINS"
