from __future__ import annotations

from ghostcash.analytics.metrics import (
    NormalizedMetrics,
    click_rate,
    conversion_rate,
    engagement_rate,
    normalize_buffer_metrics,
    revenue_per_1000_views,
    revenue_per_view,
)


def test_normalize_buffer_metrics_maps_known_types():
    raw = [
        {"type": "views", "name": "Views", "value": 1000.0, "unit": "count"},
        {"type": "reactions", "name": "Reactions", "value": 50.0, "unit": "count"},
        {"type": "comments", "name": "Comments", "value": 5.0, "unit": "count"},
        {"type": "reposts", "name": "Reposts", "value": 3.0, "unit": "count"},
    ]
    metrics = normalize_buffer_metrics(raw)
    assert metrics.views == 1000
    assert metrics.likes == 50
    assert metrics.comments == 5
    assert metrics.shares == 3


def test_normalize_buffer_metrics_prefers_views_over_impressions_and_reach():
    raw = [
        {"type": "impressions", "name": "Impressions", "value": 500.0, "unit": "count"},
        {"type": "reach", "name": "Reach", "value": 400.0, "unit": "count"},
        {"type": "views", "name": "Views", "value": 1000.0, "unit": "count"},
    ]
    metrics = normalize_buffer_metrics(raw)
    assert metrics.views == 1000


def test_normalize_buffer_metrics_falls_back_to_impressions_without_views():
    raw = [{"type": "impressions", "name": "Impressions", "value": 500.0, "unit": "count"}]
    metrics = normalize_buffer_metrics(raw)
    assert metrics.views == 500


def test_normalize_buffer_metrics_never_reports_clicks_or_profile_visits():
    # Buffer's normalized PostMetricType list has no click or
    # profile-visit metric for any GhostCash platform today (verified
    # against developers.buffer.com/guides/post-metrics.html) - these
    # must stay None (unavailable), never 0.
    raw = [{"type": "views", "name": "Views", "value": 100.0, "unit": "count"}]
    metrics = normalize_buffer_metrics(raw)
    assert metrics.clicks is None
    assert metrics.profile_visits is None


def test_normalize_buffer_metrics_ignores_unknown_types():
    raw = [
        {"type": "views", "name": "Views", "value": 100.0, "unit": "count"},
        {"type": "some_future_metric_type", "name": "?", "value": 999.0, "unit": "count"},
    ]
    metrics = normalize_buffer_metrics(raw)
    assert metrics.views == 100


def test_normalize_buffer_metrics_empty_list():
    metrics = normalize_buffer_metrics([])
    assert metrics == NormalizedMetrics()


def test_engagement_rate_computes_percentage():
    metrics = NormalizedMetrics(views=1000, likes=50, comments=10, shares=5)
    assert engagement_rate(metrics) == 6.5


def test_engagement_rate_none_without_views():
    metrics = NormalizedMetrics(views=0, likes=5)
    assert engagement_rate(metrics) is None


def test_click_rate_none_when_clicks_unavailable():
    metrics = NormalizedMetrics(views=1000, clicks=None)
    assert click_rate(metrics) is None


def test_click_rate_computes_percentage_when_available():
    metrics = NormalizedMetrics(views=1000, clicks=50)
    assert click_rate(metrics) == 5.0


def test_conversion_rate_uses_clicks_when_available():
    metrics = NormalizedMetrics(views=1000, clicks=100, sales=10)
    assert conversion_rate(metrics) == 10.0


def test_conversion_rate_falls_back_to_views_when_clicks_unavailable():
    metrics = NormalizedMetrics(views=1000, clicks=None, sales=10)
    assert conversion_rate(metrics) == 1.0


def test_conversion_rate_none_with_zero_denominator():
    metrics = NormalizedMetrics(views=0, clicks=None, sales=0)
    assert conversion_rate(metrics) is None


def test_revenue_per_view_and_per_1000_views():
    metrics = NormalizedMetrics(views=1000, revenue=10.0)
    assert revenue_per_view(metrics) == 0.01
    assert revenue_per_1000_views(metrics) == 10.0


def test_revenue_per_view_none_without_views():
    metrics = NormalizedMetrics(views=0, revenue=10.0)
    assert revenue_per_view(metrics) is None
    assert revenue_per_1000_views(metrics) is None
