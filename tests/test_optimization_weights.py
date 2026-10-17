from __future__ import annotations

from ghostcash.optimization.weights import (
    get_category_weights,
    get_hook_type_weights,
    get_template_weights,
    top_value,
    update_weights,
)


def test_weights_are_empty_before_anything_is_learned(migrated_conn):
    assert get_category_weights(migrated_conn) == {}
    assert get_hook_type_weights(migrated_conn) == {}
    assert get_template_weights(migrated_conn) == {}


def test_top_value_of_empty_weights_is_none():
    assert top_value({}) is None


def test_top_value_returns_highest_multiplier():
    assert top_value({"A": 0.8, "B": 1.5, "C": 1.0}) == "B"


def test_update_weights_persists_all_three_dimensions(migrated_conn):
    update_weights(
        migrated_conn,
        category_observations={"EXCEL": 1.5},
        hook_type_observations={"QUESTION": 1.3},
        template_observations={"LIST": 1.2},
        alpha=1.0,  # fully replace on first observation
    )

    assert get_category_weights(migrated_conn) == {"EXCEL": 1.5}
    assert get_hook_type_weights(migrated_conn) == {"QUESTION": 1.3}
    assert get_template_weights(migrated_conn) == {"LIST": 1.2}


def test_update_weights_persists_across_separate_calls(migrated_conn):
    # Simulates two separate `ghostcash optimize` runs sharing one DB.
    update_weights(
        migrated_conn,
        category_observations={"EXCEL": 2.0},
        hook_type_observations={},
        template_observations={},
        alpha=1.0,
    )
    first = get_category_weights(migrated_conn)

    update_weights(
        migrated_conn,
        category_observations={},
        hook_type_observations={},
        template_observations={},
        alpha=0.3,
    )
    second = get_category_weights(migrated_conn)

    assert first == second == {"EXCEL": 2.0}  # untouched when not observed again


def test_update_weights_blends_with_exponential_moving_average(migrated_conn):
    update_weights(
        migrated_conn,
        category_observations={"EXCEL": 2.0},
        hook_type_observations={},
        template_observations={},
        alpha=1.0,
    )
    update_weights(
        migrated_conn,
        category_observations={"EXCEL": 1.0},
        hook_type_observations={},
        template_observations={},
        alpha=0.5,
    )

    # (1 - 0.5) * 2.0 + 0.5 * 1.0 = 1.5
    assert get_category_weights(migrated_conn)["EXCEL"] == 1.5


def test_update_weights_keeps_unobserved_values_unchanged(migrated_conn):
    update_weights(
        migrated_conn,
        category_observations={"EXCEL": 1.8, "AI": 0.6},
        hook_type_observations={},
        template_observations={},
        alpha=1.0,
    )
    update_weights(
        migrated_conn,
        category_observations={"EXCEL": 1.9},  # AI not observed this run
        hook_type_observations={},
        template_observations={},
        alpha=1.0,
    )

    weights = get_category_weights(migrated_conn)
    assert weights["EXCEL"] == 1.9
    assert weights["AI"] == 0.6  # kept from last run, not reset to 1.0


def test_update_weights_returns_the_updated_dicts(migrated_conn):
    result = update_weights(
        migrated_conn,
        category_observations={"EXCEL": 1.4},
        hook_type_observations={"WARNING": 0.9},
        template_observations={"INFORMATION": 1.1},
        alpha=1.0,
    )
    assert result == {
        "category": {"EXCEL": 1.4},
        "hook_type": {"WARNING": 0.9},
        "template": {"INFORMATION": 1.1},
    }
