from __future__ import annotations

import pytest

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.config.schema import Config
from ghostcash.optimization.replication import ReplicationError, replicate_winners
from tests.conftest import FakeAIProvider


def _insert_winner(conn, **overrides) -> int:
    defaults = dict(
        topic="Excel shortcuts",
        category="EXCEL",
        title="title",
        hook="3 Excel shortcuts nobody uses",
        script="Most people never learn keyboard shortcuts in Excel. It saves hours.",
        cta="Follow for more",
        content_type="INFORMATION",
        status="WINNER",
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


def _variant_payload(**overrides) -> dict:
    payload = {
        "alternative_hooks": [
            "Are you still doing Excel like this?",
            "Stop wasting hours in Excel.",
            "Here's a faster way to use Excel.",
            "Why nobody taught you this Excel trick.",
            "The Excel shortcut that saves your week.",
        ],
        "alternative_scripts": [
            "Script variant one explores the same idea from a different angle.",
            "Script variant two explores the same idea from a different angle.",
            "Script variant three explores the same idea from a different angle.",
            "Script variant four explores the same idea from a different angle.",
            "Script variant five explores the same idea from a different angle.",
        ],
        "alternative_ctas": [
            "Comment your favorite tip",
            "Save this for later",
            "Share with a coworker",
        ],
        "related_topics": ["Google Sheets shortcuts", "Word shortcuts", "PowerPoint shortcuts"],
    }
    payload.update(overrides)
    return payload


class _AlwaysFailsProvider(AIProvider):
    def generate(self, prompt: str, **kwargs) -> str:
        raise AIProviderError("boom")

    def structured_generate(self, prompt: str, schema: dict) -> dict:
        raise AIProviderError("boom")

    def check_connectivity(self) -> str:
        raise AIProviderError("boom")


def test_replicate_winners_returns_empty_when_no_winners(migrated_conn):
    provider = FakeAIProvider(content_response=_variant_payload())
    results = replicate_winners(provider, migrated_conn, Config())
    assert results == []


def test_replicate_winners_inserts_variants_and_marks_replicated(migrated_conn):
    winner_id = _insert_winner(migrated_conn)
    provider = FakeAIProvider(content_response=_variant_payload())

    results = replicate_winners(provider, migrated_conn, Config())

    assert len(results) == 1
    result = results[0]
    assert result.content_id == winner_id
    assert result.hooks_generated == 5
    assert result.scripts_generated == 5
    assert result.ctas_generated == 3
    assert result.topics_generated == 3
    assert result.variant_content_id is not None
    assert result.experiment_key == f"winner-hook-replication:{winner_id}"

    variant_rows = migrated_conn.execute(
        "SELECT variant_type, COUNT(*) AS n FROM content_variants "
        "WHERE parent_content_id = ? GROUP BY variant_type",
        (winner_id,),
    ).fetchall()
    counts = {row["variant_type"]: row["n"] for row in variant_rows}
    assert counts == {"HOOK": 5, "SCRIPT": 5, "CTA": 3, "TOPIC": 3}

    winner_row = migrated_conn.execute(
        "SELECT winner_replicated_at FROM content WHERE id = ?", (winner_id,)
    ).fetchone()
    assert winner_row["winner_replicated_at"] is not None


def test_replicate_winners_creates_a_hook_ab_test_experiment(migrated_conn):
    winner_id = _insert_winner(migrated_conn)
    provider = FakeAIProvider(content_response=_variant_payload())

    results = replicate_winners(provider, migrated_conn, Config())
    variant_content_id = results[0].variant_content_id

    experiment = migrated_conn.execute(
        "SELECT * FROM experiments WHERE control_content_id = ?", (winner_id,)
    ).fetchone()
    assert experiment is not None
    assert experiment["variable"] == "hook"
    assert experiment["variant_content_id"] == variant_content_id
    assert experiment["status"] == "PENDING"
    assert experiment["metric_name"] == "engagement_rate"


def test_replicate_winners_variant_content_reuses_script_and_is_not_a_duplicate(migrated_conn):
    winner_id = _insert_winner(migrated_conn)
    provider = FakeAIProvider(content_response=_variant_payload())

    results = replicate_winners(provider, migrated_conn, Config())
    variant_id = results[0].variant_content_id

    winner_script = migrated_conn.execute(
        "SELECT script FROM content WHERE id = ?", (winner_id,)
    ).fetchone()["script"]
    variant_row = migrated_conn.execute(
        "SELECT script, status, hook FROM content WHERE id = ?", (variant_id,)
    ).fetchone()

    assert variant_row["script"] == winner_script
    # Despite being a byte-identical script to an existing content row,
    # it must not be rejected as a near-duplicate (check_duplicates=False
    # for exactly this controlled-experiment case).
    assert variant_row["status"] == "APPROVED"
    assert variant_row["hook"] != "3 Excel shortcuts nobody uses"


def test_replicate_winners_skips_already_replicated_winners(migrated_conn):
    _insert_winner(migrated_conn)
    provider = FakeAIProvider(content_response=_variant_payload())

    first = replicate_winners(provider, migrated_conn, Config())
    assert len(first) == 1

    second = replicate_winners(provider, migrated_conn, Config())
    assert second == []


def test_replicate_winners_respects_max_winners_replicated_per_run(migrated_conn):
    for _ in range(5):
        _insert_winner(migrated_conn)
    provider = FakeAIProvider(content_response=_variant_payload())
    config = Config()
    config.optimization.max_winners_replicated_per_run = 2

    results = replicate_winners(provider, migrated_conn, config)

    assert len(results) == 2


def test_replicate_winners_zero_limit_returns_empty(migrated_conn):
    _insert_winner(migrated_conn)
    provider = FakeAIProvider(content_response=_variant_payload())
    config = Config()
    config.optimization.max_winners_replicated_per_run = 0

    assert replicate_winners(provider, migrated_conn, config) == []


def test_replicate_winners_raises_when_every_winner_fails(migrated_conn):
    _insert_winner(migrated_conn)
    _insert_winner(migrated_conn)
    provider = _AlwaysFailsProvider()

    with pytest.raises(ReplicationError):
        replicate_winners(provider, migrated_conn, Config())


def test_replicate_winners_raises_only_when_all_fail_no_usable_variants(migrated_conn):
    winner_id = _insert_winner(migrated_conn)
    provider = FakeAIProvider(
        content_response={
            "alternative_hooks": [],
            "alternative_scripts": [],
            "alternative_ctas": [],
            "related_topics": [],
        }
    )

    # The one winner due gets an empty response -> no usable variants ->
    # every attempt fails -> the whole call raises.
    with pytest.raises(ReplicationError):
        replicate_winners(provider, migrated_conn, Config())

    row = migrated_conn.execute(
        "SELECT winner_replicated_at FROM content WHERE id = ?", (winner_id,)
    ).fetchone()
    assert row["winner_replicated_at"] is None


class _SecondCallSucceedsProvider(AIProvider):
    """Fails the first structured_generate call, succeeds on every call
    after that - for exercising replicate_winners()'s "best-effort
    across items, raise only on total failure" behavior with a mix of
    outcomes."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> str:
        raise AIProviderError("boom")

    def structured_generate(self, prompt: str, schema: dict) -> dict:
        self.calls += 1
        if self.calls == 1:
            raise AIProviderError("boom")
        return _variant_payload()

    def check_connectivity(self) -> str:
        return "ok"


def test_replicate_winners_continues_past_a_single_failure(migrated_conn):
    failing_winner_id = _insert_winner(migrated_conn, topic="First winner")
    succeeding_winner_id = _insert_winner(migrated_conn, topic="Second winner")
    provider = _SecondCallSucceedsProvider()

    results = replicate_winners(provider, migrated_conn, Config())

    assert len(results) == 1
    assert results[0].content_id == succeeding_winner_id

    failing_row = migrated_conn.execute(
        "SELECT winner_replicated_at FROM content WHERE id = ?", (failing_winner_id,)
    ).fetchone()
    succeeding_row = migrated_conn.execute(
        "SELECT winner_replicated_at FROM content WHERE id = ?", (succeeding_winner_id,)
    ).fetchone()
    assert failing_row["winner_replicated_at"] is None
    assert succeeding_row["winner_replicated_at"] is not None
