"""Analytics collection and Winner Score evaluation (CLAUDE.md sections 28-29)."""

from ghostcash.analytics.collector import (
    CollectionResult,
    collect_analytics,
    evaluate_winners,
    select_trackable_published_posts,
)
from ghostcash.analytics.metrics import (
    NormalizedMetrics,
    click_rate,
    conversion_rate,
    engagement_rate,
    normalize_buffer_metrics,
    revenue_per_1000_views,
    revenue_per_view,
)
from ghostcash.analytics.winner import (
    WinnerScoreResult,
    compute_winner_score,
    normalize_component_value,
)

__all__ = [
    "CollectionResult",
    "NormalizedMetrics",
    "WinnerScoreResult",
    "click_rate",
    "collect_analytics",
    "compute_winner_score",
    "conversion_rate",
    "engagement_rate",
    "evaluate_winners",
    "normalize_buffer_metrics",
    "normalize_component_value",
    "revenue_per_1000_views",
    "revenue_per_view",
    "select_trackable_published_posts",
]
