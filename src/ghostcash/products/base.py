"""Store interface (CLAUDE.md section 26).

    class Store:
        def create_product(...)
        def update_product(...)
        def get_sales(...)
        def get_product(...)

Implemented by GumroadStore (products/gumroad.py). Kept provider-
agnostic on purpose, same as Publisher/AIProvider elsewhere, so a
second store could implement this interface later without touching
opportunities.py or the CLI.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class StoreError(Exception):
    """Raised for any store provider failure: network, auth, validation,
    or an operation the provider genuinely does not support (see
    GumroadStore.create_product/update_product)."""


@dataclass
class StoreProduct:
    """A product as reported by the store - not GhostCash's own Product
    row (models/product.py), though the two are linked via
    Product.external_product_id."""

    external_id: str
    name: str
    price: float | None = None
    currency: str | None = None
    url: str | None = None
    published: bool = True


@dataclass
class Sale:
    """One sale as reported by the store, normalized enough for
    products/orders.py to turn into an Order row without knowing
    anything provider-specific."""

    external_sale_id: str
    external_product_id: str
    amount: float
    currency: str
    customer_email: str | None = None
    external_order_id: str | None = None
    status: str = "COMPLETED"
    ordered_at: str | None = None


@dataclass
class SalesPage:
    sales: list[Sale] = field(default_factory=list)
    next_page_key: str | None = None


class Store(ABC):
    @abstractmethod
    def create_product(
        self, name: str, price: float, description: str = "", currency: str = "usd"
    ) -> StoreProduct:
        """Create a new product in the store.

        Raises StoreError if the provider doesn't support this (see
        GumroadStore - today it never does) or the call fails.
        """

    @abstractmethod
    def update_product(self, external_id: str, **fields: Any) -> StoreProduct:
        """Update an existing product's fields in the store.

        Raises StoreError if the provider doesn't support this or the
        call fails.
        """

    @abstractmethod
    def get_product(self, external_id: str) -> StoreProduct:
        """Fetch one product by the store's own id/permalink."""

    @abstractmethod
    def get_sales(self, after: str | None = None, page_key: str | None = None) -> SalesPage:
        """Fetch one page of sales, optionally only those after a given
        ISO date. Callers follow `SalesPage.next_page_key` to page
        through the full feed (see products/orders.py:sync_orders)."""
