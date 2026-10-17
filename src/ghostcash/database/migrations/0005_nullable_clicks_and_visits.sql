-- 0005_nullable_clicks_and_visits.sql
-- Makes `clicks` and `profile_visits` nullable on content_performance
-- and analytics_snapshots (previously NOT NULL DEFAULT 0 from Phase 1).
--
-- Phase 7 (Analytics) needs NULL to mean "this platform does not expose
-- this metric", which is a different fact from "this metric is zero".
-- Verified against developers.buffer.com/guides/post-metrics.html
-- (2026-10-03): Buffer's normalized PostMetricType values are reactions,
-- reposts, comments, shares, impressions, reach, views, saves, follows,
-- quotes, viewers, totalTimeWatched - there is no click or profile-visit
-- metric for any GhostCash platform today. Collapsing "unavailable" into
-- 0 would silently tell the Winner Score calculation that every post
-- has a 0% click-through rate, which is false, not merely unmeasured.
--
-- SQLite can't drop a NOT NULL constraint in place, so this recreates
-- both tables with the wider (nullable) columns and copies existing
-- rows across, same pattern as 0004.

CREATE TABLE content_performance_new (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id               INTEGER NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    platform_id              INTEGER REFERENCES platforms(id),
    views                    INTEGER NOT NULL DEFAULT 0,
    likes                    INTEGER NOT NULL DEFAULT 0,
    comments                 INTEGER NOT NULL DEFAULT 0,
    shares                   INTEGER NOT NULL DEFAULT 0,
    clicks                   INTEGER,
    profile_visits           INTEGER,
    sales                    INTEGER NOT NULL DEFAULT 0,
    revenue                  REAL NOT NULL DEFAULT 0,
    engagement_rate          REAL,
    click_rate               REAL,
    conversion_rate          REAL,
    revenue_per_view         REAL,
    revenue_per_1000_views   REAL,
    winner_score             REAL,
    collected_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO content_performance_new (
    id, content_id, platform_id, views, likes, comments, shares,
    clicks, profile_visits, sales, revenue, engagement_rate, click_rate,
    conversion_rate, revenue_per_view, revenue_per_1000_views,
    winner_score, collected_at
)
SELECT
    id, content_id, platform_id, views, likes, comments, shares,
    clicks, profile_visits, sales, revenue, engagement_rate, click_rate,
    conversion_rate, revenue_per_view, revenue_per_1000_views,
    winner_score, collected_at
FROM content_performance;

DROP TABLE content_performance;
ALTER TABLE content_performance_new RENAME TO content_performance;

CREATE INDEX idx_content_performance_content ON content_performance(content_id);

CREATE TABLE analytics_snapshots_new (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type              TEXT NOT NULL CHECK (entity_type IN ('CONTENT', 'PLATFORM', 'PRODUCT', 'GLOBAL')),
    entity_id                INTEGER,
    metric_date              TEXT NOT NULL,
    views                    INTEGER NOT NULL DEFAULT 0,
    likes                    INTEGER NOT NULL DEFAULT 0,
    comments                 INTEGER NOT NULL DEFAULT 0,
    shares                   INTEGER NOT NULL DEFAULT 0,
    clicks                   INTEGER,
    profile_visits           INTEGER,
    sales                    INTEGER NOT NULL DEFAULT 0,
    revenue                  REAL NOT NULL DEFAULT 0,
    engagement_rate          REAL,
    click_rate               REAL,
    conversion_rate          REAL,
    revenue_per_view         REAL,
    revenue_per_1000_views   REAL,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO analytics_snapshots_new (
    id, entity_type, entity_id, metric_date, views, likes, comments,
    shares, clicks, profile_visits, sales, revenue, engagement_rate,
    click_rate, conversion_rate, revenue_per_view, revenue_per_1000_views,
    created_at
)
SELECT
    id, entity_type, entity_id, metric_date, views, likes, comments,
    shares, clicks, profile_visits, sales, revenue, engagement_rate,
    click_rate, conversion_rate, revenue_per_view, revenue_per_1000_views,
    created_at
FROM analytics_snapshots;

DROP TABLE analytics_snapshots;
ALTER TABLE analytics_snapshots_new RENAME TO analytics_snapshots;

CREATE INDEX idx_analytics_snapshots_entity ON analytics_snapshots(entity_type, entity_id);
CREATE INDEX idx_analytics_snapshots_date ON analytics_snapshots(metric_date);
