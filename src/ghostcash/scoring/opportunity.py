"""Opportunity Score (CLAUDE.md section 12).

    Opportunity Score =
        0.20 curiosity
      + 0.20 shareability
      + 0.20 commercial relevance
      + 0.15 evergreen value
      + 0.15 audience usefulness
      + 0.10 production simplicity
      - competition penalty

Weights come from config.yaml `scoring_weights` (configurable, not
hard-coded permanently, per the spec) rather than the numbers above,
which are only the shipped defaults.

Callers pass whichever sub-scores they actually have. At topic-
generation time (Phase 2) there is no meaningful "audience usefulness"
score yet - that's better judged once a real script exists - so it's
simply omitted here and contributes zero; content-generation time
(Phase 3) is expected to supply it too, once evaluated.
"""

from __future__ import annotations

from collections.abc import Mapping

from ghostcash.config.schema import ScoringWeights

_ADDITIVE_KEYS = (
    "curiosity",
    "shareability",
    "commercial",
    "evergreen",
    "usefulness",
    "production_simplicity",
)


def compute_opportunity_score(scores: Mapping[str, float], weights: ScoringWeights) -> float:
    total = sum(getattr(weights, key) * scores[key] for key in _ADDITIVE_KEYS if key in scores)
    if "competition_penalty" in scores:
        total -= weights.competition_penalty * scores["competition_penalty"]
    return total
