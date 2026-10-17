-- 0004_publishing_draft_status.sql
-- Adds 'DRAFT' to published_posts.status.
--
-- CLAUDE.md section 63 ("create Buffer drafts rather than publishing")
-- and section 52 (PUBLISHING = approval_required initially) both need a
-- distinct state for a post that exists in Buffer but has NOT been
-- scheduled/sent - Buffer's own createPost mutation calls this exact
-- state 'draft' (saveToDraft: true). PENDING (GhostCash's own "not yet
-- attempted" state) and Buffer's DRAFT are different things, so this
-- adds DRAFT rather than overloading PENDING.
--
-- SQLite can't ALTER a CHECK constraint in place, so this recreates the
-- table with the wider constraint and copies existing rows across.

CREATE TABLE published_posts_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id          INTEGER NOT NULL REFERENCES content(id) ON DELETE CASCADE,
    platform_id         INTEGER NOT NULL REFERENCES platforms(id),
    campaign_id         INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    external_post_id    TEXT,
    buffer_update_id    TEXT,
    status              TEXT NOT NULL DEFAULT 'PENDING'
                           CHECK (status IN ('PENDING', 'DRAFT', 'SCHEDULED', 'PUBLISHED', 'FAILED')),
    scheduled_at        TEXT,
    published_at        TEXT,
    error               TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO published_posts_new (
    id, content_id, platform_id, campaign_id, idempotency_key,
    external_post_id, buffer_update_id, status, scheduled_at,
    published_at, error, created_at
)
SELECT
    id, content_id, platform_id, campaign_id, idempotency_key,
    external_post_id, buffer_update_id, status, scheduled_at,
    published_at, error, created_at
FROM published_posts;

DROP TABLE published_posts;
ALTER TABLE published_posts_new RENAME TO published_posts;

CREATE INDEX idx_published_posts_content ON published_posts(content_id);
CREATE INDEX idx_published_posts_status ON published_posts(status);
