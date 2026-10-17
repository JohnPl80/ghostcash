from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ghostcash.products.base import StoreError
from ghostcash.products.gumroad import GumroadStore


def _mock_response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


def test_missing_api_key_raises():
    with pytest.raises(StoreError):
        GumroadStore(api_key="")


def test_get_product_sends_bearer_auth_header():
    session = MagicMock()
    session.request.return_value = _mock_response(
        json_data={
            "success": True,
            "product": {
                "id": "abc123",
                "name": "Excel Kit",
                "price": 1900,
                "currency": "usd",
                "short_url": "https://gumroad.com/l/abc123",
                "published": True,
            },
        }
    )
    store = GumroadStore(api_key="secret", session=session)

    product = store.get_product("abc123")

    assert product.external_id == "abc123"
    assert product.name == "Excel Kit"
    assert product.price == 19.0
    assert product.url == "https://gumroad.com/l/abc123"
    args, kwargs = session.request.call_args
    assert args[0] == "GET"
    assert args[1] == "https://api.gumroad.com/v2/products/abc123"
    assert kwargs["headers"]["Authorization"] == "Bearer secret"


def test_get_product_not_found_raises_store_error():
    session = MagicMock()
    session.request.return_value = _mock_response(
        status_code=404, json_data={"success": False, "message": "Not Found"}
    )
    store = GumroadStore(api_key="secret", session=session)

    with pytest.raises(StoreError):
        store.get_product("nonexistent")


def test_create_product_raises_store_error_with_manual_step():
    # Verified against api.gumroad.com (2026-10-04): POST /products
    # returns 404 "not implemented" - this must surface as a clear
    # StoreError naming the dashboard workaround, not fake success.
    session = MagicMock()
    session.request.return_value = _mock_response(
        status_code=404, json_data={"success": False, "message": "Not Found"}
    )
    store = GumroadStore(api_key="secret", session=session)

    with pytest.raises(StoreError) as exc_info:
        store.create_product(name="New Product", price=9.99)

    message = str(exc_info.value)
    assert "does not support creating products" in message
    assert "ghostcash products link" in message


def test_update_product_raises_store_error_with_manual_step():
    session = MagicMock()
    session.request.return_value = _mock_response(
        status_code=404, json_data={"success": False, "message": "Not Found"}
    )
    store = GumroadStore(api_key="secret", session=session)

    with pytest.raises(StoreError) as exc_info:
        store.update_product("abc123", price=1500)

    assert "does not support updating products" in str(exc_info.value)


def test_get_sales_parses_sale_fields_and_pagination():
    session = MagicMock()
    session.request.return_value = _mock_response(
        json_data={
            "success": True,
            "sales": [
                {
                    "id": "sale_1",
                    "email": "buyer@example.com",
                    "product_id": "abc123",
                    "price": 1900,
                    "currency_symbol": "$",
                    "order_id": 555,
                    "created_at": "2026-10-01T00:00:00Z",
                    "refunded": False,
                    "partially_refunded": False,
                    "chargedback": False,
                }
            ],
            "next_page_key": "page2",
        }
    )
    store = GumroadStore(api_key="secret", session=session)

    page = store.get_sales()

    assert page.next_page_key == "page2"
    assert len(page.sales) == 1
    sale = page.sales[0]
    assert sale.external_sale_id == "sale_1"
    assert sale.external_product_id == "abc123"
    assert sale.amount == 19.0
    assert sale.currency == "usd"
    assert sale.customer_email == "buyer@example.com"
    assert sale.external_order_id == "555"
    assert sale.status == "COMPLETED"


def test_get_sales_maps_refund_statuses():
    session = MagicMock()

    def _sale(refunded=False, partially_refunded=False, chargedback=False):
        return {
            "id": "s",
            "product_id": "p",
            "price": 1000,
            "currency_symbol": "$",
            "refunded": refunded,
            "partially_refunded": partially_refunded,
            "chargedback": chargedback,
        }

    session.request.return_value = _mock_response(
        json_data={"success": True, "sales": [_sale(refunded=True)], "next_page_key": None}
    )
    store = GumroadStore(api_key="secret", session=session)
    page = store.get_sales()
    assert page.sales[0].status == "REFUNDED"

    session.request.return_value = _mock_response(
        json_data={"success": True, "sales": [_sale(chargedback=True)], "next_page_key": None}
    )
    page = store.get_sales()
    assert page.sales[0].status == "CHARGED_BACK"

    session.request.return_value = _mock_response(
        json_data={
            "success": True,
            "sales": [_sale(partially_refunded=True)],
            "next_page_key": None,
        }
    )
    page = store.get_sales()
    assert page.sales[0].status == "PARTIALLY_REFUNDED"


def test_get_sales_passes_pagination_params():
    session = MagicMock()
    session.request.return_value = _mock_response(
        json_data={"success": True, "sales": [], "next_page_key": None}
    )
    store = GumroadStore(api_key="secret", session=session)

    store.get_sales(after="2026-01-01", page_key="cursor1")

    _, kwargs = session.request.call_args
    assert kwargs["params"] == {"after": "2026-01-01", "page_key": "cursor1"}


def test_network_error_raises_store_error():
    import requests

    session = MagicMock()
    session.request.side_effect = requests.ConnectionError("boom")
    store = GumroadStore(api_key="secret", session=session)

    with pytest.raises(StoreError):
        store.get_product("abc123")
