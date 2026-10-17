from __future__ import annotations

from ghostcash.ai.budget import UsageTracker


def test_record_call_increments_counts(migrated_conn):
    tracker = UsageTracker(migrated_conn)
    assert tracker.calls_this_month() == 0

    tracker.record_call(prompt_tokens=100, output_tokens=50)
    tracker.record_call(prompt_tokens=10, output_tokens=5)

    assert tracker.calls_this_month() == 2
    assert tracker.prompt_tokens_this_month() == 110
    assert tracker.output_tokens_this_month() == 55


def test_record_call_without_token_counts_still_counts_the_call(migrated_conn):
    tracker = UsageTracker(migrated_conn)
    tracker.record_call()
    assert tracker.calls_this_month() == 1
    assert tracker.prompt_tokens_this_month() == 0


def test_usage_persists_across_tracker_instances(migrated_conn):
    UsageTracker(migrated_conn).record_call(prompt_tokens=1, output_tokens=2)
    # A fresh instance against the same connection should see the same data.
    tracker2 = UsageTracker(migrated_conn)
    assert tracker2.calls_this_month() == 1
    assert tracker2.prompt_tokens_this_month() == 1
