"""Order recording and revenue attribution (CLAUDE.md section 27).

`sync_orders()` pages through the store's sales feed and upserts each
sale into the `orders` table (added in migration 0006 - section 27
requires recording "order, customer, product, amount, currency,
timestamp, status" but the Phase 1 schema never added a table for it).
`external_sale_id` is UNIQUE, so re-running this is always safe
(section 57) - a sale already on file is updated in place (so a later
refund is reflected) rather than inserted again.

Revenue attribution: Gumroad's sales data carries no per-content
referrer - it cannot say which specific video drove a given sale, only
which *product* was bought. The simplest reliable, honest option
available is to distribute each sale's revenue across the content
items currently promoting that product (content.product_id matches),
weighted by each one's own view count - content that drew more views
gets credited with proportionally more of the resulting revenue. This
is an approximation, stated as one: it is not a per-content "this view
converted" fact, just a reasonable way to let real sales data flow
back into Winner Score's revenue component (analytics/winner.py)
instead of leaving it permanently at zero for every product. When no
qualifying content has any views yet, revenue is left unattributed
(the order itself is still recorded faithfully either way) rather than
split evenly with no real signal behind it.

A refund or chargeback reaching GhostCash on a later sync reverses its
previously-attributed share the same way, via a negative delta -
`amount_delta` is just (new effective amount - old effective amount),
where a non-COMPLETED sale's effective amount is 0. One code path
handles both a brand new sale (old effective = 0) and a later refund
(new effective = 0) correctly.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ghostcash.analytics.metrics import NormalizedMetrics, revenue_per_1000_views, revenue_per_view
from ghostcash.models.product import Order
from ghostcash.products.base import Sale, Store

# Safety valve against an unbounded pagination loop - 50 pages at
# Gumroad's typical page size is far more sales than a system this size
# (CLAUDE.md section 4 - a few hundred EUR/month business) will have
# before the next sync catches up.
_MAX_PAGES = 50

# Orders in these statuses contribute zero net revenue for attribution
# purposes - a refund/chargeback is a sale that, economically, didn't
# happen.
_ZERO_REVENUE_STATUSES: frozenset[str] = frozenset({"REFUNDED", "CHARGED_BACK"})


@dataclass
class SyncResult:
    sales_seen: int = 0
    orders_created: int = 0
    orders_updated: int = 0
    revenue_attributed: float = 0.0
    revenue_unattributed: float = 0.0


def _effective_amount(amount: float, status: str) -> float:
    if status in _ZERO_REVENUE_STATUSES:
        return 0.0
    # Partially refunded: Gumroad's sale.price already reflects the
    # original price, not a reduced one - there is no partial-refund
    # amount in the fields this was verified against (see
    # products/gumroad.py), so this is treated as still-effective at
    # its full recorded amount rather than guessing a deduction.
    return amount


def sync_orders(store: Store, conn: sqlite3.Connection) -> SyncResult:
    result = SyncResult()
    page_key: str | None = None

    for _ in range(_MAX_PAGES):
        page = store.get_sales(page_key=page_key)
        for sale in page.sales:
            result.sales_seen += 1
            created, delta = _upsert_order(conn, sale)
            if created:
                result.orders_created += 1
            elif delta != 0.0:
                result.orders_updated += 1

            if delta != 0.0:
                attributed = _attribute_revenue_delta(conn, sale.external_product_id, delta)
                result.revenue_attributed += attributed
                result.revenue_unattributed += delta - attributed

        if not page.next_page_key:
            break
        page_key = page.next_page_key

    conn.commit()
    return result


def _resolve_product_id(conn: sqlite3.Connection, external_product_id: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM products WHERE external_product_id = ?", (external_product_id,)
    ).fetchone()
    return row["id"] if row else None


def _upsert_order(conn: sqlite3.Connection, sale: Sale) -> tuple[bool, float]:
    """Insert or update one order row.

    Returns (created, amount_delta) where amount_delta is the change in
    *effective* revenue this order represents (see _effective_amount) -
    positive for a new completed sale, negative for a sale that just
    became refunded/charged-back, zero if nothing economically changed.
    """
    product_id = _resolve_product_id(conn, sale.external_product_id)
    existing = conn.execute(
        "SELECT amount, status FROM orders WHERE external_sale_id = ?", (sale.external_sale_id,)
    ).fetchone()

    new_effective = _effective_amount(sale.amount, sale.status)

    if existing is None:
        conn.execute(
            """
            INSERT INTO orders (
                product_id, external_sale_id, external_order_id, customer_email,
                amount, currency, status, ordered_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                sale.external_sale_id,
                sale.external_order_id,
                sale.customer_email,
                sale.amount,
                sale.currency,
                sale.status,
                sale.ordered_at,
            ),
        )
        return True, new_effective

    old_effective = _effective_amount(existing["amount"], existing["status"])
    conn.execute(
        "UPDATE orders SET amount = ?, status = ?, product_id = ? WHERE external_sale_id = ?",
        (sale.amount, sale.status, product_id, sale.external_sale_id),
    )
    return False, new_effective - old_effective


def _attribute_revenue_delta(
    conn: sqlite3.Connection, external_product_id: str, delta: float
) -> float:
    """Distribute `delta` across content_performance rows for content
    currently promoting this product, weighted by each row's views.
    Returns how much was actually attributed (0.0 if no qualifying
    content has any views to weight by)."""
    product_id = _resolve_product_id(conn, external_product_id)
    if product_id is None:
        return 0.0

    perf_rows = conn.execute(
        """
        SELECT cp.id, cp.views, cp.likes, cp.comments, cp.shares, cp.clicks,
               cp.profile_visits, cp.sales, cp.revenue
        FROM content_performance cp
        JOIN content c ON c.id = cp.content_id
        WHERE c.product_id = ? AND c.status NOT IN ('REJECTED', 'FAILED')
        """,
        (product_id,),
    ).fetchall()

    total_views = sum(row["views"] or 0 for row in perf_rows)
    if total_views <= 0:
        return 0.0

    attributed = 0.0
    for row in perf_rows:
        weight = (row["views"] or 0) / total_views
        share = delta * weight
        new_revenue = (row["revenue"] or 0.0) + share
        metrics = NormalizedMetrics(
            views=row["views"] or 0,
            likes=row["likes"] or 0,
            comments=row["comments"] or 0,
            shares=row["shares"] or 0,
            clicks=row["clicks"],
            profile_visits=row["profile_visits"],
            sales=row["sales"] or 0,
            revenue=new_revenue,
        )
        conn.execute(
            "UPDATE content_performance SET revenue = ?, revenue_per_view = ?, "
            "revenue_per_1000_views = ? WHERE id = ?",
            (new_revenue, revenue_per_view(metrics), revenue_per_1000_views(metrics), row["id"]),
        )
        attributed += share

    return attributed


def list_orders(conn: sqlite3.Connection, product_id: int | None = None) -> list[Order]:
    if product_id is not None:
        rows = conn.execute(
            "SELECT * FROM orders WHERE product_id = ? ORDER BY created_at DESC", (product_id,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return [Order.from_row(row) for row in rows]


def total_revenue_for_product(conn: sqlite3.Connection, product_id: int) -> float:
    row = conn.execute(
        "SELECT SUM(amount) AS total FROM orders WHERE product_id = ? AND status = 'COMPLETED'",
        (product_id,),
    ).fetchone()
    return float(row["total"] or 0.0)
