"""Winner Score (CLAUDE.md section 29).

    Winner Score =
      0.25 engagement
    + 0.25 click-through
    + 0.35 revenue
    + 0.15 retention/other available quality metrics

"If revenue data is unavailable, redistribute weights automatically."
This module generalizes that one sentence to every component: whichever
of engagement/click_through/revenue/retention can actually be computed
for a given content item (see analytics/collector.py:_build_components
for how "available" is decided) keeps its relative share of the
*configured* weights, scaled up to sum to 1.0. A component is never
silently treated as 0 just because the data to compute it doesn't exist
yet - that would penalize content for a measurement gap, not a real
performance problem.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ghostcash.config.schema import WinnerWeights

_COMPONENT_NAMES: tuple[str, ...] = ("engagement", "click_through", "revenue", "retention")


@dataclass
class WinnerScoreResult:
    score: float
    components_used: list[str] = field(default_factory=list)
    components_missing: list[str] = field(default_factory=list)


def normalize_component_value(raw_value: float, target: float) -> float:
    """Clamp raw_value/target into [0, 1].

    `target` is the rate/value that counts as a "fully met" score of
    1.0 for this component (see AnalyticsConfig.target_* fields) -
    content doing better than target still scores a flat 1.0 rather than
    an unbounded number, so one viral outlier can't single-handedly
    dominate every other component's contribution to the final score.
    """
    if target <= 0:
        return 0.0
    return max(0.0, min(1.0, raw_value / target))


def compute_winner_score(
    components: dict[str, float],
    weights: WinnerWeights,
) -> WinnerScoreResult:
    """Combine whichever of engagement/click_through/revenue/retention
    are present in `components` (each already normalized to [0, 1] by
    normalize_component_value) into a single Winner Score, redistributing
    the configured weight of any missing component proportionally among
    the components that ARE present.

    `components` should only contain keys for components that could
    actually be computed - callers decide availability (e.g. revenue
    requires an attached product; see collector.py), not this function.
    """
    available = [name for name in _COMPONENT_NAMES if name in components]
    missing = [name for name in _COMPONENT_NAMES if name not in components]

    if not available:
        return WinnerScoreResult(score=0.0, components_used=[], components_missing=missing)

    configured_weights = {name: getattr(weights, name) for name in _COMPONENT_NAMES}
    available_weight_total = sum(configured_weights[name] for name in available)

    if available_weight_total <= 0:
        # Every available component is configured with 0 weight (an
        # unusual config, but not invalid) - split evenly rather than
        # dividing by zero.
        redistributed = {name: 1.0 / len(available) for name in available}
    else:
        redistributed = {
            name: configured_weights[name] / available_weight_total for name in available
        }

    score = sum(components[name] * redistributed[name] for name in available)
    return WinnerScoreResult(
        score=score,
        components_used=available,
        components_missing=missing,
    )
