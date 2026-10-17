from __future__ import annotations

import pytest

from ghostcash.analytics.winner import compute_winner_score, normalize_component_value
from ghostcash.config.schema import WinnerWeights


def test_normalize_component_value_clamps_to_unit_interval():
    assert normalize_component_value(5.0, target=10.0) == 0.5
    assert normalize_component_value(20.0, target=10.0) == 1.0  # clamped, doesn't exceed 1.0
    assert normalize_component_value(-5.0, target=10.0) == 0.0  # clamped at 0


def test_normalize_component_value_zero_target_is_zero():
    assert normalize_component_value(5.0, target=0.0) == 0.0


def test_compute_winner_score_all_components_present_uses_configured_weights():
    weights = WinnerWeights(engagement=0.25, click_through=0.25, revenue=0.35, retention=0.15)
    components = {"engagement": 1.0, "click_through": 1.0, "revenue": 1.0, "retention": 1.0}
    result = compute_winner_score(components, weights)
    assert result.score == pytest.approx(1.0)
    assert set(result.components_used) == {"engagement", "click_through", "revenue", "retention"}
    assert result.components_missing == []


def test_compute_winner_score_redistributes_weight_of_missing_revenue():
    # CLAUDE.md section 29: "If revenue data is unavailable, redistribute
    # weights automatically." With revenue missing, engagement/
    # click_through/retention should divide up its 0.35 share
    # proportionally to their own configured weights.
    weights = WinnerWeights(engagement=0.25, click_through=0.25, revenue=0.35, retention=0.15)
    components = {"engagement": 1.0, "click_through": 0.0, "retention": 0.0}

    result = compute_winner_score(components, weights)

    # engagement's share of the 0.25+0.25+0.15=0.65 available weight is
    # 0.25/0.65, and only engagement scores 1.0, so the final score
    # equals that redistributed weight.
    expected = 0.25 / 0.65
    assert result.score == pytest.approx(expected)
    assert "revenue" in result.components_missing
    assert "revenue" not in result.components_used


def test_compute_winner_score_no_components_available_scores_zero():
    weights = WinnerWeights()
    result = compute_winner_score({}, weights)
    assert result.score == 0.0
    assert result.components_used == []
    assert set(result.components_missing) == {"engagement", "click_through", "revenue", "retention"}


def test_compute_winner_score_only_engagement_available_gets_full_weight():
    weights = WinnerWeights(engagement=0.25, click_through=0.25, revenue=0.35, retention=0.15)
    result = compute_winner_score({"engagement": 0.5}, weights)
    # Only component available -> takes 100% of the redistributed weight.
    assert result.score == pytest.approx(0.5)
    assert result.components_used == ["engagement"]


def test_compute_winner_score_ignores_extra_unknown_keys():
    weights = WinnerWeights()
    result = compute_winner_score({"engagement": 1.0, "nonsense": 999.0}, weights)
    assert "nonsense" not in result.components_used
