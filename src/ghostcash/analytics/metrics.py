"""Metric normalization (CLAUDE.md section 28).

GhostCash only has one analytics source today (Buffer), but this module
is kept provider-agnostic on purpose - a normalized shape in, rate
calculations out - so a second platform's metrics API can plug in later
without touching anything downstream (scoring, CLI, dashboard).

Buffer's normalized PostMetricType values, verified against
developers.buffer.com/guides/post-metrics.html on 2026-10-03:
reactions, reposts, comments, shares, impressions, reach, views, saves,
follows, quotes, viewers, totalTimeWatched. There is no click or
profile-visit type in that list for any platform Buffer currently
supports posting to (YouTube/TikTok/Instagram - and TikTok posting isn't
even supported, see publishing/buffer.py). That is a real limitation of
the data available today, not a bug here: `clicks` and `profile_visits`
are left as None (unavailable) rather than guessed at or defaulted to 0,
and click_rate() below propagates that None rather than silently
reporting 0%.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Buffer sometimes reports more than one "how many people saw this" type
# for the same post (e.g. both impressions and reach). Preference order
# for picking a single `views` figure when more than one is present.
_VIEW_TYPE_PRIORITY: tuple[str, ...] = ("views", "impressions", "reach")

# type -> which NormalizedMetrics field it adds to.
_LIKE_TYPES: frozenset[str] = frozenset({"reactions"})
_COMMENT_TYPES: frozenset[str] = frozenset({"comments"})
_SHARE_TYPES: frozenset[str] = frozenset({"reposts", "shares", "quotes"})


@dataclass
class NormalizedMetrics:
    """Platform-agnostic metrics for one post, after normalization.

    clicks/profile_visits are `int | None`: None means "this platform
    does not expose this metric" (see module docstring), distinct from a
    real, reported zero. sales/revenue are always present because they
    come from GhostCash's own order records (Phase 8), not the platform.
    """

    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    clicks: int | None = None
    profile_visits: int | None = None
    sales: int = 0
    revenue: float = 0.0


def normalize_buffer_metrics(raw_metrics: list[dict[str, Any]]) -> NormalizedMetrics:
    """Convert Buffer's `post.metrics` array into a NormalizedMetrics.

    `raw_metrics` items look like {"type": "views", "name": "Views",
    "value": 123.0, "unit": "count"}. Unknown/future metric types are
    ignored rather than raising, so a new PostMetricType Buffer adds
    later doesn't break collection - it's simply not counted yet.
    """
    by_type: dict[str, float] = {}
    for metric in raw_metrics:
        metric_type = str(metric.get("type", ""))
        value = metric.get("value")
        if metric_type and isinstance(value, (int, float)):
            by_type[metric_type] = float(value)

    views = 0
    for candidate in _VIEW_TYPE_PRIORITY:
        if candidate in by_type:
            views = int(by_type[candidate])
            break

    likes = int(sum(by_type.get(t, 0) for t in _LIKE_TYPES))
    comments = int(sum(by_type.get(t, 0) for t in _COMMENT_TYPES))
    shares = int(sum(by_type.get(t, 0) for t in _SHARE_TYPES))

    return NormalizedMetrics(
        views=views,
        likes=likes,
        comments=comments,
        shares=shares,
        # Buffer exposes no click/profile-visit metric today - see
        # module docstring. Left unavailable rather than defaulted to 0.
        clicks=None,
        profile_visits=None,
    )


def engagement_rate(metrics: NormalizedMetrics) -> float | None:
    """(likes + comments + shares) / views, as a percentage.

    None if views is 0 - there is nothing to divide by, not a 0% rate.
    """
    if metrics.views <= 0:
        return None
    return (metrics.likes + metrics.comments + metrics.shares) / metrics.views * 100


def click_rate(metrics: NormalizedMetrics) -> float | None:
    """clicks / views, as a percentage. None if clicks is unavailable."""
    if metrics.clicks is None or metrics.views <= 0:
        return None
    return metrics.clicks / metrics.views * 100


def conversion_rate(metrics: NormalizedMetrics) -> float | None:
    """sales / clicks, as a percentage, when there's a click-through
    funnel to measure. Falls back to sales / views when clicks aren't
    available, since that's still a real (if looser) conversion signal -
    but returns None rather than fabricating a rate from zero traffic."""
    denominator = metrics.clicks if metrics.clicks is not None else metrics.views
    if not denominator or denominator <= 0:
        return None
    return metrics.sales / denominator * 100


def revenue_per_view(metrics: NormalizedMetrics) -> float | None:
    if metrics.views <= 0:
        return None
    return metrics.revenue / metrics.views


def revenue_per_1000_views(metrics: NormalizedMetrics) -> float | None:
    per_view = revenue_per_view(metrics)
    if per_view is None:
        return None
    return per_view * 1000
