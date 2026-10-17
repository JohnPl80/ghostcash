-- 0001_initial.sql
-- Core GhostCash schema. See CLAUDE.md section 6 for the required table list.
-- Timestamps are stored as ISO-8601 UTC text for portability and readability.

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------
-- Topic discovery & scoring (sections 11-12, 49)
-- ---------------------------------------------------------------------

CREATE TABLE topics (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    topic                  TEXT NOT NULL,
    category               TEXT NOT NULL,
    hook                   TEXT,
    target_audience        TEXT,
    freshness              TEXT NOT NULL DEFAULT 'EVERGREEN'
                               CHECK (freshness IN ('EVERGREEN', 'TRENDING', 'TIME_SENSITIVE')),
    expires_at             TEXT,
    evergreen_score        REAL,
    curiosity_score        REAL,
    shareability_score     REAL,
    commercial_score       REAL,
    production_cost_score  REAL,
    competition_score      REAL,
    overall_score          REAL,
    status                 TEXT NOT NULL DEFAULT 'CANDIDATE'
                               CHECK (status IN ('CANDIDATE', 'SELECTED', 'REJECTED', 'USED')),
    created_at             TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_topics_category ON topics(category);
CREATE INDEX idx_topics_status ON topics(status);

CREATE TABLE topic_scores (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                      INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    curiosity_score               REAL,
    shareability_score            REAL,
    commercial_score              REAL,
    evergreen_score               REAL,
    usefulness_score              REAL,
    production_simplicity_score   REAL,
    competition_penalty           REAL,
    overall_score                 REAL,
    weights_json                  TEXT,
    created_at                    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_topic_scores_topic_id ON topic_scores(topic_id);

-- ---------------------------------------------------------------------
-- Products & product opportunities (sections 23-27)
-- ---------------------------------------------------------------------

CREATE TABLE products (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    description  TEXT,
    price        REAL,
    url          TEXT,
    category     TEXT,
    status       TEXT NOT NULL DEFAULT 'DRAFT'
                    CHECK (status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'ACTIVE', 'ARCHIVED')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE product_opportunities (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    proposed_name           TEXT NOT NULL,
    problem_solved          TEXT,
    contents_outline        TEXT,
    price_recommendation    REAL,
    target_customer         TEXT,
    sales_page_copy         TEXT,
    supporting_views        INTEGER NOT NULL DEFAULT 0,
    supporting_clicks       INTEGER NOT NULL DEFAULT 0,
    existing_product_id     INTEGER REFERENCES products(id) ON DELETE SET NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING_APPROVAL'
                               CHECK (status IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED')),
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_product_opportunities_status ON product_opportunities(status);

-- ---------------------------------------------------------------------
-- Content (section 7) & its variants (section 30)
-- ---------------------------------------------------------------------

CREATE TABLE content (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id           INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    topic              TEXT NOT NULL,
    category           TEXT NOT NULL,
    title              TEXT,
    hook               TEXT,
    script             TEXT,
    caption            TEXT,
    cta                TEXT,
    product_id         INTEGER REFERENCES products(id) ON DELETE SET NULL,
    content_type       TEXT,
    status             TEXT NOT NULL DEFAULT 'IDEA'
                          CHECK (status IN (
                              'IDEA', 'RESEARCHING', 'DRAFT', 'APPROVED', 'RENDERING', 'READY',
                              'SCHEDULED', 'PUBLISHED', 'ANALYZING', 'WINNER', 'REJECTED', 'FAILED'
                          )),
    quality_score      REAL,
    opportunity_score  REAL,
    fingerprint        TEXT,
    created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    scheduled_at       TEXT,
    published_at       TEXT
);

CREATE INDEX idx_content_status ON content(status);
CREATE INDEX idx_content_category ON content(category);
CREATE INDEX idx_content_fingerprint ON content(fingerprint);

CREATE TABLE content_variants (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_content_id   INTEGER NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    variant_type        TEXT NOT NULL CHECK (variant_type IN ('HOOK', 'SCRIPT', 'CTA', 'TOPIC')),
    variant_text        TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'PROPOSED'
                           CHECK (status IN ('PROPOSED', 'TESTING', 'WINNER', 'REJECTED')),
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_content_variants_parent ON content_variants(parent_content_id);

-- ---------------------------------------------------------------------
-- Platforms, campaigns & publishing (sections 19-22, 57)
-- ---------------------------------------------------------------------

CREATE TABLE platforms (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE CHECK (name IN ('YOUTUBE', 'TIKTOK', 'INSTAGRAM')),
    buffer_channel_id   TEXT,
    is_active           INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    config_json         TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE campaigns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    description  TEXT,
    category     TEXT,
    status       TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'PAUSED', 'COMPLETED')),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE published_posts (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id          INTEGER NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    platform_id         INTEGER NOT NULL REFERENCES platforms(id),
    campaign_id         INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    external_post_id    TEXT,
    buffer_update_id    TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING'
                           CHECK (status IN ('PENDING', 'SCHEDULED', 'PUBLISHED', 'FAILED')),
    scheduled_at        TEXT,
    published_at        TEXT,
    error               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_published_posts_content ON published_posts(content_id);
CREATE INDEX idx_published_posts_status ON published_posts(status);

-- ---------------------------------------------------------------------
-- Analytics & winner detection (sections 28-29)
-- ---------------------------------------------------------------------

CREATE TABLE content_performance (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id               INTEGER NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    platform_id              INTEGER REFERENCES platforms(id),
    views                    INTEGER NOT NULL DEFAULT 0,
    likes                    INTEGER NOT NULL DEFAULT 0,
    comments                 INTEGER NOT NULL DEFAULT 0,
    shares                   INTEGER NOT NULL DEFAULT 0,
    clicks                   INTEGER NOT NULL DEFAULT 0,
    profile_visits           INTEGER NOT NULL DEFAULT 0,
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

CREATE INDEX idx_content_performance_content ON content_performance(content_id);

CREATE TABLE analytics_snapshots (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type              TEXT NOT NULL CHECK (entity_type IN ('CONTENT', 'PLATFORM', 'PRODUCT', 'GLOBAL')),
    entity_id                INTEGER,
    metric_date              TEXT NOT NULL,
    views                    INTEGER NOT NULL DEFAULT 0,
    likes                    INTEGER NOT NULL DEFAULT 0,
    comments                 INTEGER NOT NULL DEFAULT 0,
    shares                   INTEGER NOT NULL DEFAULT 0,
    clicks                   INTEGER NOT NULL DEFAULT 0,
    profile_visits           INTEGER NOT NULL DEFAULT 0,
    sales                    INTEGER NOT NULL DEFAULT 0,
    revenue                  REAL NOT NULL DEFAULT 0,
    engagement_rate          REAL,
    click_rate               REAL,
    conversion_rate          REAL,
    revenue_per_view         REAL,
    revenue_per_1000_views   REAL,
    created_at               TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_analytics_snapshots_entity ON analytics_snapshots(entity_type, entity_id);
CREATE INDEX idx_analytics_snapshots_date ON analytics_snapshots(metric_date);

-- ---------------------------------------------------------------------
-- Jobs & failure management (section 37)
-- ---------------------------------------------------------------------

CREATE TABLE jobs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type          TEXT NOT NULL,
    idempotency_key   TEXT UNIQUE,
    status            TEXT NOT NULL DEFAULT 'PENDING'
                         CHECK (status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED')),
    attempt           INTEGER NOT NULL DEFAULT 0,
    started_at        TEXT,
    finished_at       TEXT,
    error             TEXT,
    stack_trace       TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_jobs_type_status ON jobs(job_type, status);

CREATE TABLE job_errors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id          INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    attempt         INTEGER NOT NULL,
    error_message   TEXT,
    stack_trace     TEXT,
    occurred_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_job_errors_job ON job_errors(job_id);

-- ---------------------------------------------------------------------
-- System events & settings (sections 39-41, 46)
-- ---------------------------------------------------------------------

CREATE TABLE system_events (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type     TEXT NOT NULL,
    component      TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'INFO' CHECK (severity IN ('INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    message        TEXT,
    metadata_json  TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_system_events_component ON system_events(component);
CREATE INDEX idx_system_events_created_at ON system_events(created_at);

CREATE TABLE settings (
    key          TEXT PRIMARY KEY,
    value        TEXT,
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
