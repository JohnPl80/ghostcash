"""Product abstraction, product-opportunity detection, Gumroad store
adapter, and order recording (CLAUDE.md sections 23-27)."""

from ghostcash.products.activation import ActivationError, link_product
from ghostcash.products.base import Sale, SalesPage, Store, StoreError, StoreProduct
from ghostcash.products.factory import get_store
from ghostcash.products.gumroad import GumroadStore
from ghostcash.products.opportunities import (
    OpportunityDetectionError,
    approve_opportunity,
    detect_opportunities,
    get_opportunity,
    list_opportunities,
    reject_opportunity,
)
from ghostcash.products.orders import (
    SyncResult,
    list_orders,
    sync_orders,
    total_revenue_for_product,
)

__all__ = [
    "ActivationError",
    "GumroadStore",
    "OpportunityDetectionError",
    "Sale",
    "SalesPage",
    "Store",
    "StoreError",
    "StoreProduct",
    "SyncResult",
    "approve_opportunity",
    "detect_opportunities",
    "get_opportunity",
    "get_store",
    "link_product",
    "list_opportunities",
    "list_orders",
    "reject_opportunity",
    "sync_orders",
    "total_revenue_for_product",
]
