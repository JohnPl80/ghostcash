"""Linking an approved opportunity to a real Gumroad product (CLAUDE.md
section 24's "system publishes/activates product" step).

Gumroad's API cannot create or edit products (see products/gumroad.py)
- the owner has to actually build the listing in the Gumroad dashboard
themselves first. This module is what runs *after* that manual step:
given the permalink/id the owner just created, it verifies the product
genuinely exists on Gumroad (never trusts an unverified id), creates
GhostCash's own `products` row for it, and attaches that product to
every piece of content under the opportunity's topic so Winner Score's
revenue component (analytics/winner.py) and future content generation
both know a product now exists for this topic.

`product_opportunities.existing_product_id` is reused here as the
"this opportunity resulted in this product" marker once linking
succeeds - it's set, on the same table, with the same meaning in
spirit ("a product covers this topic") that it has for an opportunity
rejected for the opposite reason (coverage found at detection time).
Reusing the column avoids a schema change for what is, underneath,
the same fact.
"""

from __future__ import annotations

import sqlite3

from ghostcash.models.product import Product
from ghostcash.products.base import Store
from ghostcash.products.opportunities import get_opportunity


class ActivationError(Exception):
    """Raised when linking an opportunity to a store product fails."""


def link_product(
    conn: sqlite3.Connection,
    store: Store,
    opportunity_id: int,
    external_product_id: str,
) -> Product:
    """Verify `external_product_id` exists on the store, create the
    local Product row for it, and attach it to the opportunity's topic.

    Raises ActivationError if the opportunity isn't APPROVED yet, has
    already been linked, or StoreError if the product doesn't actually
    exist on Gumroad.
    """
    opportunity = get_opportunity(conn, opportunity_id)
    if opportunity is None:
        raise ActivationError(f"No product opportunity with id {opportunity_id}.")
    if opportunity.status != "APPROVED":
        raise ActivationError(
            f"Opportunity {opportunity_id} is {opportunity.status}, not APPROVED - "
            "run `ghostcash products approve` first."
        )
    if opportunity.existing_product_id is not None:
        raise ActivationError(
            f"Opportunity {opportunity_id} is already linked to product "
            f"{opportunity.existing_product_id}."
        )

    store_product = store.get_product(external_product_id)

    now_product = Product(
        name=store_product.name or opportunity.proposed_name,
        description=opportunity.problem_solved,
        price=(
            store_product.price
            if store_product.price is not None
            else opportunity.price_recommendation
        ),
        url=store_product.url,
        category=_topic_category(conn, opportunity.topic_id),
        status="ACTIVE",
        external_product_id=store_product.external_id,
        store_provider="gumroad",
    )
    cursor = conn.execute(
        """
        INSERT INTO products (name, description, price, url, category, status,
                               external_product_id, store_provider)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_product.name,
            now_product.description,
            now_product.price,
            now_product.url,
            now_product.category,
            now_product.status,
            now_product.external_product_id,
            now_product.store_provider,
        ),
    )
    now_product.id = cursor.lastrowid

    if opportunity.topic_id is not None:
        conn.execute(
            "UPDATE content SET product_id = ? WHERE topic_id = ? AND product_id IS NULL",
            (now_product.id, opportunity.topic_id),
        )

    conn.execute(
        "UPDATE product_opportunities SET existing_product_id = ? WHERE id = ?",
        (now_product.id, opportunity_id),
    )
    conn.commit()
    return now_product


def _topic_category(conn: sqlite3.Connection, topic_id: int | None) -> str | None:
    if topic_id is None:
        return None
    row = conn.execute("SELECT category FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return row["category"] if row else None
