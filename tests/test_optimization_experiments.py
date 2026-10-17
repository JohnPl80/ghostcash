from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ghostcash.config.schema import Config
from ghostcash.optimization.experiments import evaluate_experiments


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


def _insert_performance(conn, content_id, **overrides) -> None:
    defaults = dict(views=0, likes=0, comments=0, shares=0)
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO content_performance (content_id, views, likes, comments, shares) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            content_id,
            defaults["views"],
            defaults["likes"],
            defaults["comments"],
            defaults["shares"],
        ),
    )
    conn.commit()


def _insert_experiment(
    conn, control_id, variant_id, content_variant_id=None, created_at=None
) -> str:
    key = f"winner-hook-replication:{control_id}"
    conn.execute(
        "INSERT INTO experiments (experiment_key, hypothesis, variable, control_description, "
        "variant_description, control_content_id, variant_content_id, content_variant_id, "
        "metric_name, status, created_at) "
        "VALUES (?, 'h', 'hook', 'orig', 'alt', ?, ?, ?, 'engagement_rate', 'PENDING', "
        "COALESCE(?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))",
        (key, control_id, variant_id, content_variant_id, created_at),
    )
    conn.commit()
    return key


def _insert_content_variant(conn, parent_id) -> int:
    cursor = conn.execute(
        "INSERT INTO content_variants (parent_content_id, variant_type, variant_text, status) "
        "VALUES (?, 'HOOK', 'alt hook', 'TESTING')",
        (parent_id,),
    )
    conn.commit()
    return cursor.lastrowid


def test_evaluate_experiments_returns_empty_when_none_pending(migrated_conn):
    assert evaluate_experiments(migrated_conn, Config()) == []


def test_evaluate_experiments_not_ready_without_enough_views(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    _insert_performance(migrated_conn, control_id, views=10, likes=1)
    _insert_performance(migrated_conn, variant_id, views=10, likes=1)
    _insert_experiment(migrated_conn, control_id, variant_id)

    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 200

    results = evaluate_experiments(migrated_conn, config)

    assert results == []
    row = migrated_conn.execute("SELECT status FROM experiments").fetchone()
    assert row["status"] == "PENDING"


def test_evaluate_experiments_variant_wins_on_practical_margin(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    variant_row_id = _insert_content_variant(migrated_conn, control_id)
    # control: 1000 views, 20 engagements (likes+comments+shares) -> 2.0%
    _insert_performance(migrated_conn, control_id, views=1000, likes=10, comments=5, shares=5)
    # variant: 1000 views, 50 engagements -> 5.0% (+150% relative, well past 10% threshold)
    _insert_performance(migrated_conn, variant_id, views=1000, likes=30, comments=10, shares=10)
    _insert_experiment(migrated_conn, control_id, variant_id, content_variant_id=variant_row_id)

    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 200
    config.optimization.practical_significance_threshold = 0.10

    results = evaluate_experiments(migrated_conn, config)

    assert len(results) == 1
    assert results[0].result == "VARIANT_WINS"

    experiment_row = migrated_conn.execute("SELECT * FROM experiments").fetchone()
    assert experiment_row["status"] == "EVALUATED"
    assert experiment_row["result"] == "VARIANT_WINS"
    assert experiment_row["decision"]

    variant_status = migrated_conn.execute(
        "SELECT status FROM content_variants WHERE id = ?", (variant_row_id,)
    ).fetchone()["status"]
    assert variant_status == "WINNER"


def test_evaluate_experiments_control_wins_marks_variant_rejected(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    variant_row_id = _insert_content_variant(migrated_conn, control_id)
    _insert_performance(migrated_conn, control_id, views=1000, likes=30, comments=10, shares=10)
    _insert_performance(migrated_conn, variant_id, views=1000, likes=10, comments=5, shares=5)
    _insert_experiment(migrated_conn, control_id, variant_id, content_variant_id=variant_row_id)

    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 200
    config.optimization.practical_significance_threshold = 0.10

    results = evaluate_experiments(migrated_conn, config)

    assert results[0].result == "CONTROL_WINS"
    variant_status = migrated_conn.execute(
        "SELECT status FROM content_variants WHERE id = ?", (variant_row_id,)
    ).fetchone()["status"]
    assert variant_status == "REJECTED"


def test_evaluate_experiments_inconclusive_within_threshold(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    _insert_performance(migrated_conn, control_id, views=1000, likes=20, comments=0, shares=0)
    _insert_performance(migrated_conn, variant_id, views=1000, likes=21, comments=0, shares=0)
    _insert_experiment(migrated_conn, control_id, variant_id)

    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 200
    config.optimization.practical_significance_threshold = 0.10

    results = evaluate_experiments(migrated_conn, config)

    assert results[0].result == "INCONCLUSIVE"


def test_evaluate_experiments_times_out_after_configured_days(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    # Neither arm ever reaches the view threshold.
    _insert_performance(migrated_conn, control_id, views=5, likes=1)
    _insert_performance(migrated_conn, variant_id, views=5, likes=1)
    old_created_at = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _insert_experiment(migrated_conn, control_id, variant_id, created_at=old_created_at)

    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 200
    config.optimization.experiment_timeout_days = 21

    results = evaluate_experiments(migrated_conn, config)

    assert len(results) == 1
    assert results[0].result == "INCONCLUSIVE"
    row = migrated_conn.execute("SELECT status FROM experiments").fetchone()
    assert row["status"] == "EVALUATED"


def test_evaluate_experiments_not_timed_out_yet_stays_pending(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    _insert_performance(migrated_conn, control_id, views=5, likes=1)
    _insert_performance(migrated_conn, variant_id, views=5, likes=1)
    recent_created_at = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    _insert_experiment(migrated_conn, control_id, variant_id, created_at=recent_created_at)

    config = Config()
    config.optimization.min_views_for_experiment_evaluation = 200
    config.optimization.experiment_timeout_days = 21

    results = evaluate_experiments(migrated_conn, config)

    assert results == []
    row = migrated_conn.execute("SELECT status FROM experiments").fetchone()
    assert row["status"] == "PENDING"


def test_evaluate_experiments_handles_deleted_content_gracefully(migrated_conn):
    control_id = _insert_content(migrated_conn)
    variant_id = _insert_content(migrated_conn)
    _insert_experiment(migrated_conn, control_id, variant_id)
    # ON DELETE SET NULL - simulate the referenced content having been
    # removed after the experiment was created.
    migrated_conn.execute("DELETE FROM content WHERE id = ?", (variant_id,))
    migrated_conn.commit()

    old_created_at = (datetime.now(UTC) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    migrated_conn.execute(
        "UPDATE experiments SET created_at = ?, variant_content_id = NULL", (old_created_at,)
    )
    migrated_conn.commit()

    config = Config()
    results = evaluate_experiments(migrated_conn, config)

    assert len(results) == 1
    assert results[0].result == "INCONCLUSIVE"
