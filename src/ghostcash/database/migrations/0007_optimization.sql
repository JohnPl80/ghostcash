-- 0007_optimization.sql
-- Autonomous optimization loop support (CLAUDE.md sections 30, 50-51).

-- Idempotency marker for winner replication (section 30): once a WINNER
-- content item has had its hooks/scripts/CTAs/related topics generated,
-- this is set so `ghostcash optimize` never re-replicates it on a later
-- run (section 56/57 - scheduled jobs must be idempotent).
ALTER TABLE content ADD COLUMN winner_replicated_at TEXT;

-- Experiment log (section 51). One row per control-vs-variant comparison
-- the optimization loop sets up (today: always a HOOK variant tested
-- against the winning content it was replicated from - see
-- optimization/replication.py). `status` tracks whether the comparison
-- has enough data to judge yet; `result`/`decision` are filled in once
-- it does (or once it times out - see
-- config.optimization.experiment_timeout_days).
CREATE TABLE experiments (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_key        TEXT NOT NULL UNIQUE,
    hypothesis            TEXT NOT NULL,
    variable              TEXT NOT NULL,
    control_description   TEXT NOT NULL,
    variant_description   TEXT NOT NULL,
    control_content_id    INTEGER REFERENCES content(id) ON DELETE SET NULL,
    variant_content_id    INTEGER REFERENCES content(id) ON DELETE SET NULL,
    content_variant_id    INTEGER REFERENCES content_variants(id) ON DELETE SET NULL,
    metric_name           TEXT NOT NULL,
    control_value         REAL,
    variant_value         REAL,
    status                TEXT NOT NULL DEFAULT 'PENDING'
                             CHECK (status IN ('PENDING', 'EVALUATED')),
    result                TEXT CHECK (result IN ('CONTROL_WINS', 'VARIANT_WINS', 'INCONCLUSIVE')),
    decision              TEXT,
    created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    evaluated_at          TEXT
);

CREATE INDEX idx_experiments_status ON experiments(status);
CREATE INDEX idx_experiments_variant_content ON experiments(variant_content_id);
