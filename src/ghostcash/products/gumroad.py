"""GumroadStore (CLAUDE.md sections 23-27).

Implemented against Gumroad's current official API, verified
2026-10-04 against api.gumroad.com/v2 (via its current documentation
mirror and help.gumroad.com) rather than recalled from training data.
That verification surfaced a real, load-bearing limitation:

    POST /v2/products        (create)  -> documented as "not currently
    PUT  /v2/products/:id    (update)  ->  implemented, returns a 404.
                                            Use the Gumroad dashboard."

Both are explicitly, consistently documented as unimplemented - this
isn't a guess or an API key/scope problem. GET /v2/products,
GET /v2/products/:id, and GET /v2/sales, by contrast, are real and
working today.

This has a real consequence for CLAUDE.md section 24's "owner approves
-> system publishes/activates product" flow: the "system publishes"
half can't mean "the system creates the Gumroad listing", because
nothing can do that except the dashboard. create_product() and
update_product() are real calls against the real endpoints (not stubs),
and they fail exactly the way the API actually fails, with a message
telling the operator the one manual step that actually works: create
or edit the product at https://app.gumroad.com/products, then run
`ghostcash products link <opportunity_id> <permalink>` to attach
GhostCash's record of it. See README "Products" section.

Authentication: `Authorization: Bearer <token>` header. The token comes
from Settings -> Advanced -> Applications -> Create application ->
Generate access token (help.gumroad.com/article/280) - this is a
personal access token in practice even though Gumroad's docs frame it
as an OAuth app token; no redirect flow is needed to use it.
"""

from __future__ import annotations

from typing import Any

import requests

from ghostcash.products.base import Sale, SalesPage, Store, StoreError, StoreProduct

API_BASE = "https://api.gumroad.com/v2"

# Gumroad's sale object has no ISO currency code field, only a display
# symbol (verified 2026-10-04 - "currency_symbol": "$", no "currency"
# field in the documented response shape). Several currencies share a
# symbol (CAD/AUD/USD all use "$"), so this is a best-effort, documented
# approximation, not a fabricated fact - it defaults to "usd" (Gumroad's
# overwhelmingly common case) for anything not recognized.
_SYMBOL_TO_CURRENCY: dict[str, str] = {
    "$": "usd",
    "€": "eur",
    "£": "gbp",
    "¥": "jpy",
}

_MANUAL_STEP = (
    "Create or edit the product at https://app.gumroad.com/products yourself, then "
    "run `ghostcash products link <opportunity_id> <gumroad permalink>` (see README "
    "'Products') to attach GhostCash's record to it."
)


class GumroadStore(Store):
    def __init__(
        self, api_key: str, session: requests.Session | None = None, timeout_seconds: int = 60
    ) -> None:
        if not api_key:
            raise StoreError(
                "Gumroad API key is required. Set GUMROAD_API_KEY in your .env - generate one "
                "at https://app.gumroad.com/settings/advanced under Applications "
                "(see help.gumroad.com/article/280-create-application-api.html)."
            )
        self._api_key = api_key
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

    def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        try:
            response = self._session.request(
                method,
                f"{API_BASE}{path}",
                headers={"Authorization": f"Bearer {self._api_key}"},
                params=params,
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise StoreError(f"Could not reach the Gumroad API: {exc}") from exc

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise StoreError(f"Gumroad returned a non-JSON response: {exc}") from exc

        if not response.ok or not payload.get("success", response.ok):
            message = payload.get("message") or payload.get("error") or response.text
            raise StoreError(f"Gumroad API error (HTTP {response.status_code}): {message}")

        return payload

    # -- section 26: Store interface --------------------------------------

    def create_product(
        self, name: str, price: float, description: str = "", currency: str = "usd"
    ) -> StoreProduct:
        try:
            payload = self._request(
                "POST",
                "/products",
                params={"name": name, "price": int(round(price * 100)), "description": description},
            )
        except StoreError as exc:
            raise StoreError(
                "Gumroad's public API does not support creating products (confirmed "
                f"against POST {API_BASE}/products, which returns 404 - verified "
                f"2026-10-04). {_MANUAL_STEP}"
            ) from exc
        return self._parse_product(payload["product"])

    def update_product(self, external_id: str, **fields: Any) -> StoreProduct:
        try:
            payload = self._request("PUT", f"/products/{external_id}", params=fields)
        except StoreError as exc:
            raise StoreError(
                f"Gumroad's public API does not support updating products (confirmed against "
                f"PUT {API_BASE}/products/:id, which returns 404 - verified 2026-10-04). "
                f"{_MANUAL_STEP}"
            ) from exc
        return self._parse_product(payload["product"])

    def get_product(self, external_id: str) -> StoreProduct:
        payload = self._request("GET", f"/products/{external_id}")
        return self._parse_product(payload["product"])

    def get_sales(self, after: str | None = None, page_key: str | None = None) -> SalesPage:
        params: dict[str, Any] = {}
        if after:
            params["after"] = after
        if page_key:
            params["page_key"] = page_key
        payload = self._request("GET", "/sales", params=params)
        sales = [self._parse_sale(raw) for raw in payload.get("sales", [])]
        return SalesPage(sales=sales, next_page_key=payload.get("next_page_key"))

    # -- parsing -----------------------------------------------------------

    @staticmethod
    def _parse_product(raw: dict[str, Any]) -> StoreProduct:
        price_cents = raw.get("price")
        return StoreProduct(
            external_id=str(raw.get("id") or raw.get("custom_permalink") or ""),
            name=str(raw.get("name", "")),
            price=(price_cents / 100) if isinstance(price_cents, (int, float)) else None,
            currency=raw.get("currency"),
            url=raw.get("short_url"),
            published=bool(raw.get("published", True)),
        )

    @staticmethod
    def _parse_sale(raw: dict[str, Any]) -> Sale:
        price_cents = raw.get("price", 0) or 0
        if raw.get("chargedback"):
            status = "CHARGED_BACK"
        elif raw.get("refunded"):
            status = "REFUNDED"
        elif raw.get("partially_refunded"):
            status = "PARTIALLY_REFUNDED"
        else:
            status = "COMPLETED"

        symbol = raw.get("currency_symbol", "$")
        currency = _SYMBOL_TO_CURRENCY.get(symbol, "usd")

        return Sale(
            external_sale_id=str(raw["id"]),
            external_product_id=str(raw.get("product_id", "")),
            amount=price_cents / 100,
            currency=currency,
            customer_email=raw.get("email") or raw.get("purchase_email"),
            external_order_id=(str(raw["order_id"]) if raw.get("order_id") is not None else None),
            status=status,
            ordered_at=raw.get("created_at"),
        )
