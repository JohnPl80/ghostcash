from __future__ import annotations

import pytest

from ghostcash.config.schema import ScoringWeights
from ghostcash.scoring.opportunity import compute_opportunity_score


def test_matches_manual_calculation_with_default_weights():
    weights = ScoringWeights()  # 0.20/0.20/0.20/0.15/0.15/0.10, penalty 0.10
    scores = {
        "curiosity": 0.8,
        "shareability": 0.6,
        "commercial": 0.5,
        "evergreen": 0.9,
        "usefulness": 0.7,
        "production_simplicity": 0.9,
        "competition_penalty": 0.3,
    }
    expected = (
        0.20 * 0.8 + 0.20 * 0.6 + 0.20 * 0.5 + 0.15 * 0.9 + 0.15 * 0.7 + 0.10 * 0.9 - 0.10 * 0.3
    )
    assert compute_opportunity_score(scores, weights) == pytest.approx(expected)


def test_missing_keys_contribute_zero():
    weights = ScoringWeights()
    # Topic-generation time has no "usefulness" score yet - omitted keys
    # should simply not contribute, not raise.
    score = compute_opportunity_score({"curiosity": 1.0}, weights)
    assert score == pytest.approx(weights.curiosity * 1.0)


def test_competition_penalty_is_subtracted():
    weights = ScoringWeights()
    with_penalty = compute_opportunity_score(
        {"curiosity": 1.0, "competition_penalty": 1.0}, weights
    )
    without_penalty = compute_opportunity_score({"curiosity": 1.0}, weights)
    assert with_penalty < without_penalty
    assert without_penalty - with_penalty == pytest.approx(weights.competition_penalty)


def test_custom_weights_are_respected():
    weights = ScoringWeights(
        curiosity=1.0,
        shareability=0.0,
        commercial=0.0,
        evergreen=0.0,
        usefulness=0.0,
        production_simplicity=0.0,
        competition_penalty=0.0,
    )
    score = compute_opportunity_score({"curiosity": 0.42, "shareability": 999}, weights)
    assert score == pytest.approx(0.42)
