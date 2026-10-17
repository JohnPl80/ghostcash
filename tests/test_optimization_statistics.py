from __future__ import annotations

from ghostcash.optimization.statistics import (
    DIMENSIONS,
    compute_performance_multipliers,
    compute_statistics,
)


def _insert_content(conn, **overrides) -> int:
    defaults = dict(
        topic="t",
        category="EXCEL",
        title="title",
        hook="Are you still doing this manually?",
        cta="Follow for more",
        content_type="INFORMATION",
        status="PUBLISHED",
        rendered_duration_seconds=25.0,
        product_id=None,
    )
    defaults.update(overrides)
    cursor = conn.execute(
        "INSERT INTO content "
        "(topic, category, title, hook, cta, content_type, status, "
        "rendered_duration_seconds, product_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            defaults["topic"],
            defaults["category"],
            defaults["title"],
            defaults["hook"],
            defaults["cta"],
            defaults["content_type"],
            defaults["status"],
            defaults["rendered_duration_seconds"],
            defaults["product_id"],
        ),
    )
    conn.commit()
    return cursor.lastrowid


def _insert_platform(conn, name="YOUTUBE") -> int:
    cursor = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES (?, ?)", (name, "ch1")
    )
    conn.commit()
    return cursor.lastrowid


def _insert_performance(conn, content_id, platform_id, **overrides) -> None:
    defaults = dict(views=1000, winner_score=50.0, engagement_rate=5.0, revenue_per_1000_views=2.0)
    defaults.update(overrides)
    conn.execute(
        "INSERT INTO content_performance "
        "(content_id, platform_id, views, winner_score, engagement_rate, revenue_per_1000_views) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            content_id,
            platform_id,
            defaults["views"],
            defaults["winner_score"],
            defaults["engagement_rate"],
            defaults["revenue_per_1000_views"],
        ),
    )
    conn.commit()


def test_compute_statistics_returns_empty_for_every_dimension_with_no_data(migrated_conn):
    stats = compute_statistics(migrated_conn)
    assert set(stats.keys()) == set(DIMENSIONS)
    assert all(stats[dim] == [] for dim in DIMENSIONS)


def test_compute_statistics_ignores_performance_rows_without_a_winner_score(migrated_conn):
    content_id = _insert_content(migrated_conn)
    platform_id = _insert_platform(migrated_conn)
    _insert_performance(migrated_conn, content_id, platform_id, winner_score=None)

    stats = compute_statistics(migrated_conn)
    assert stats["CATEGORY"] == []


def test_compute_statistics_buckets_by_category(migrated_conn):
    platform_id = _insert_platform(migrated_conn)
    excel_id = _insert_content(migrated_conn, category="EXCEL")
    ai_id = _insert_content(migrated_conn, category="AI")
    _insert_performance(migrated_conn, excel_id, platform_id, winner_score=80.0)
    _insert_performance(migrated_conn, ai_id, platform_id, winner_score=20.0)

    stats = compute_statistics(migrated_conn)
    category_values = {s.value: s.avg_winner_score for s in stats["CATEGORY"]}
    assert category_values == {"EXCEL": 80.0, "AI": 20.0}
    # Sorted best-first.
    assert stats["CATEGORY"][0].value == "EXCEL"


def test_compute_statistics_classifies_hook_type(migrated_conn):
    platform_id = _insert_platform(migrated_conn)
    content_id = _insert_content(migrated_conn, hook="3 Excel shortcuts nobody uses")
    _insert_performance(migrated_conn, content_id, platform_id)

    stats = compute_statistics(migrated_conn)
    values = {s.value for s in stats["HOOK_TYPE"]}
    assert "NUMBER_LIST" in values


def test_compute_statistics_buckets_content_length(migrated_conn):
    platform_id = _insert_platform(migrated_conn)
    short_id = _insert_content(migrated_conn, rendered_duration_seconds=15.0)
    long_id = _insert_content(migrated_conn, rendered_duration_seconds=35.0)
    _insert_performance(migrated_conn, short_id, platform_id)
    _insert_performance(migrated_conn, long_id, platform_id)

    stats = compute_statistics(migrated_conn)
    values = {s.value for s in stats["CONTENT_LENGTH"]}
    assert "<20s" in values
    assert ">30s" in values


def test_compute_statistics_platform_resolves_via_performance_row_directly(migrated_conn):
    # content_performance.platform_id is its own direct FK to platforms -
    # PLATFORM resolves from it without needing a matching published_posts
    # row at all (a performance row can exist - e.g. backfilled, or from
    # a sync that outpaced publish-status bookkeeping - before or without
    # one).
    platform_id = _insert_platform(migrated_conn)
    content_id = _insert_content(migrated_conn)
    _insert_performance(migrated_conn, content_id, platform_id)

    stats = compute_statistics(migrated_conn)
    assert {s.value for s in stats["PLATFORM"]} == {"YOUTUBE"}


def test_compute_statistics_platform_unknown_when_platform_id_is_null(migrated_conn):
    content_id = _insert_content(migrated_conn)
    _insert_performance(migrated_conn, content_id, None)

    stats = compute_statistics(migrated_conn)
    assert {s.value for s in stats["PLATFORM"]} == {"UNKNOWN"}


def test_compute_statistics_product_dimension_distinguishes_none_from_id(migrated_conn):
    platform_id = _insert_platform(migrated_conn)
    with_product = _insert_content(migrated_conn, product_id=None)
    _insert_performance(migrated_conn, with_product, platform_id)

    stats = compute_statistics(migrated_conn)
    assert {s.value for s in stats["PRODUCT"]} == {"NONE"}


def test_compute_performance_multipliers_requires_min_sample_size():
    from ghostcash.optimization.statistics import DimensionStat

    stats = [
        DimensionStat(
            "CATEGORY",
            "EXCEL",
            sample_size=2,
            avg_winner_score=90.0,
            avg_engagement_rate=None,
            avg_revenue_per_1000_views=None,
        ),
        DimensionStat(
            "CATEGORY",
            "AI",
            sample_size=10,
            avg_winner_score=50.0,
            avg_engagement_rate=None,
            avg_revenue_per_1000_views=None,
        ),
    ]
    multipliers = compute_performance_multipliers(stats, min_sample_size=5)
    assert "EXCEL" not in multipliers  # below min_sample_size
    assert "AI" in multipliers


def test_compute_performance_multipliers_normalizes_around_one():
    from ghostcash.optimization.statistics import DimensionStat

    stats = [
        DimensionStat(
            "CATEGORY",
            "EXCEL",
            sample_size=10,
            avg_winner_score=100.0,
            avg_engagement_rate=None,
            avg_revenue_per_1000_views=None,
        ),
        DimensionStat(
            "CATEGORY",
            "AI",
            sample_size=10,
            avg_winner_score=50.0,
            avg_engagement_rate=None,
            avg_revenue_per_1000_views=None,
        ),
    ]
    multipliers = compute_performance_multipliers(stats, min_sample_size=1)
    # Equal sample sizes -> overall mean is 75; EXCEL=100/75, AI=50/75.
    assert multipliers["EXCEL"] > 1.0
    assert multipliers["AI"] < 1.0


def test_compute_performance_multipliers_clips_outliers():
    from ghostcash.optimization.statistics import DimensionStat

    # Equal sample sizes can never push a two-bin ratio past 2.0 (the
    # weighted mean of two equally-weighted bins is always at least half
    # the larger one), so the outlier here needs an unequal weight: a
    # small-sample, sky-high scorer against a much larger low-scoring
    # sample pulls the weighted mean down near AI's own score, sending
    # EXCEL's raw ratio (~10.9x) and AI's (~0.01x) both well past the
    # [0.5, 2.0] clip range.
    stats = [
        DimensionStat(
            "CATEGORY",
            "EXCEL",
            sample_size=1,
            avg_winner_score=1000.0,
            avg_engagement_rate=None,
            avg_revenue_per_1000_views=None,
        ),
        DimensionStat(
            "CATEGORY",
            "AI",
            sample_size=10,
            avg_winner_score=1.0,
            avg_engagement_rate=None,
            avg_revenue_per_1000_views=None,
        ),
    ]
    multipliers = compute_performance_multipliers(
        stats, min_sample_size=1, clip_min=0.5, clip_max=2.0
    )
    assert multipliers["EXCEL"] == 2.0
    assert multipliers["AI"] == 0.5


def test_compute_performance_multipliers_empty_when_nothing_eligible():
    assert compute_performance_multipliers([], min_sample_size=5) == {}
