"""Product models (CLAUDE.md sections 23-27).

Note: per section 24, the system may generate product proposals
(ProductOpportunity) autonomously, but a human must approve before a
Product moves to ACTIVE / becomes purchasable. This gate is enforced by
the products subsystem (later phase), not by these dataclasses alone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, fields
from typing import Any

PRODUCT_STATUSES: tuple[str, ...] = ("DRAFT", "PENDING_APPROVAL", "APPROVED", "ACTIVE", "ARCHIVED")
OPPORTUNITY_STATUSES: tuple[str, ...] = ("PENDING_APPROVAL", "APPROVED", "REJECTED")
ORDER_STATUSES: tuple[str, ...] = ("COMPLETED", "REFUNDED", "PARTIALLY_REFUNDED", "CHARGED_BACK")


def _row_kwargs(row: sqlite3.Row, dataclass_cls: type) -> dict[str, Any]:
    known = {f.name for f in fields(dataclass_cls)}
    return {k: v for k, v in dict(row).items() if k in known}


@dataclass
class Product:
    name: str
    id: int | None = None
    description: str | None = None
    price: float | None = None
    url: str | None = None
    category: str | None = None
    status: str = "DRAFT"
    created_at: str | None = None
    updated_at: str | None = None
    # Phase 8: the store's own identifier for this product (e.g.
    # Gumroad's permalink) and which Store implementation it belongs to.
    # Both are set by `ghostcash products link` after the product is
    # created manually in the store's dashboard - see products/gumroad.py
    # for why that step can't be done through the API.
    external_product_id: str | None = None
    store_provider: str = "gumroad"

    def __post_init__(self) -> None:
        if self.status not in PRODUCT_STATUSES:
            raise ValueError(f"Invalid product status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Product:
        return cls(**_row_kwargs(row, cls))


@dataclass
class ProductOpportunity:
    proposed_name: str
    id: int | None = None
    topic_id: int | None = None
    problem_solved: str | None = None
    contents_outline: str | None = None
    price_recommendation: float | None = None
    target_customer: str | None = None
    sales_page_copy: str | None = None
    supporting_views: int = 0
    # None = unavailable, not zero - no GhostCash platform exposes a
    # click metric today (see analytics/metrics.py); this field is kept
    # for schema stability and in case a future platform adds one.
    supporting_clicks: int | None = None
    # The substitute "how much interest did this get" signal actually
    # available today, used in its place for detection (section 25's
    # "high views + high clicks" becomes "high views + high
    # engagement_rate" here).
    supporting_engagement_rate: float | None = None
    existing_product_id: int | None = None
    status: str = "PENDING_APPROVAL"
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in OPPORTUNITY_STATUSES:
            raise ValueError(f"Invalid opportunity status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> ProductOpportunity:
        return cls(**_row_kwargs(row, cls))


@dataclass
class Order:
    """CLAUDE.md section 27 - "The system must record: order, customer,
    product, amount, currency, timestamp, status." No orders table
    existed before Phase 8 even though section 6's table list didn't
    mention one; this fills that gap.

    external_sale_id is the store's own id for this sale (Gumroad's
    `sale.id`) and is UNIQUE in the schema - it is the idempotency key
    (section 57) that makes re-syncing the sales feed safe to repeat.
    """

    external_sale_id: str
    id: int | None = None
    product_id: int | None = None
    external_order_id: str | None = None
    customer_email: str | None = None
    amount: float = 0.0
    currency: str = "usd"
    status: str = "COMPLETED"
    ordered_at: str | None = None
    created_at: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ORDER_STATUSES:
            raise ValueError(f"Invalid order status: {self.status!r}")

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Order:
        return cls(**_row_kwargs(row, cls))
