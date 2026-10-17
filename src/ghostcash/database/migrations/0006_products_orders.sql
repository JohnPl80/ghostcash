-- 0006_products_orders.sql
-- Phase 8 (CLAUDE.md sections 23-27): product store wiring + order
-- recording.
--
-- 1. products gets external_product_id (the store's own product
--    identifier - e.g. Gumroad's permalink/id) and store_provider.
--    Both are plain nullable/defaulted columns, so a simple ADD COLUMN
--    is enough (no table recreate needed).
--
-- 2. product_opportunities.supporting_clicks becomes nullable, and a
--    new supporting_engagement_rate column is added. Verified against
--    Gumroad's and Buffer's current APIs: no GhostCash platform exposes
--    a click metric (see migration 0005 / analytics/metrics.py), so
--    section 25's "high views + high clicks" signal can never actually
--    be computed from real data. supporting_engagement_rate is the
--    substitute "how much interest did this get" signal this system
--    actually has available; supporting_clicks is kept (nullable, for
--    schema stability and in case a future platform exposes it) but
--    will be NULL for every opportunity detected today - that's software
--    being honest about a real data gap, not a bug.
--    SQLite can't drop a NOT NULL constraint in place, so this recreates
--    the table, same pattern as migrations 0004/0005.
--
-- 3. A new `orders` table, required by section 27 ("The system must
--    record: order, customer, product, amount, currency, timestamp,
--    status") but missing from the Phase 1 schema - section 6's table
--    list didn't include it even though section 27 explicitly requires
--    it, so this fills that gap. external_sale_id is UNIQUE and is the
--    idempotency key (section 57) for syncing Gumroad's sales feed
--    without ever double-recording the same sale.

ALTER TABLE products ADD COLUMN external_product_id TEXT;
ALTER TABLE products ADD COLUMN store_provider TEXT NOT NULL DEFAULT 'gumroad';

CREATE TABLE product_opportunities_new (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    topic_id                  INTEGER REFERENCES topics(id) ON DELETE SET NULL,
    proposed_name             TEXT NOT NULL,
    problem_solved            TEXT,
    contents_outline          TEXT,
    price_recommendation      REAL,
    target_customer           TEXT,
    sales_page_copy           TEXT,
    supporting_views          INTEGER NOT NULL DEFAULT 0,
    supporting_clicks         INTEGER,
    supporting_engagement_rate REAL,
    existing_product_id       INTEGER REFERENCES products(id) ON DELETE SET NULL,
    status                    TEXT NOT NULL DEFAULT 'PENDING_APPROVAL'
                                 CHECK (status IN ('PENDING_APPROVAL', 'APPROVED', 'REJECTED')),
    created_at                TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT INTO product_opportunities_new (
    id, topic_id, proposed_name, problem_solved, contents_outline,
    price_recommendation, target_customer, sales_page_copy,
    supporting_views, supporting_clicks, existing_product_id, status,
    created_at
)
SELECT
    id, topic_id, proposed_name, problem_solved, contents_outline,
    price_recommendation, target_customer, sales_page_copy,
    supporting_views, supporting_clicks, existing_product_id, status,
    created_at
FROM product_opportunities;

DROP TABLE product_opportunities;
ALTER TABLE product_opportunities_new RENAME TO product_opportunities;

CREATE INDEX idx_product_opportunities_status ON product_opportunities(status);

CREATE TABLE orders (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id          INTEGER REFERENCES products(id) ON DELETE SET NULL,
    external_sale_id    TEXT NOT NULL UNIQUE,
    external_order_id   TEXT,
    customer_email      TEXT,
    amount              REAL NOT NULL DEFAULT 0,
    currency            TEXT NOT NULL DEFAULT 'usd',
    status              TEXT NOT NULL DEFAULT 'COMPLETED'
                           CHECK (status IN (
                               'COMPLETED', 'REFUNDED', 'PARTIALLY_REFUNDED', 'CHARGED_BACK'
                           )),
    ordered_at          TEXT,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX idx_orders_product ON orders(product_id);
