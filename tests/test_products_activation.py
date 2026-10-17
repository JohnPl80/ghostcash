from __future__ import annotations

import pytest

from ghostcash.products.activation import ActivationError, link_product
from ghostcash.products.base import Store, StoreProduct


class _FakeStore(Store):
    def __init__(self, product: StoreProduct | None = None, error: Exception | None = None):
        self.product = product or StoreProduct(
            external_id="excelkit",
            name="Excel Kit",
            price=19.0,
            currency="usd",
            url="https://gumroad.com/l/excelkit",
        )
        self.error = error
        self.get_product_calls: list[str] = []

    def create_product(self, name, price, description="", currency="usd"):
        raise NotImplementedError

    def update_product(self, external_id, **fields):
        raise NotImplementedError

    def get_product(self, external_id):
        self.get_product_calls.append(external_id)
        if self.error:
            raise self.error
        return self.product

    def get_sales(self, after=None, page_key=None):
        raise NotImplementedError


def _insert_approved_opportunity(conn, topic_category="EXCEL") -> tuple[int, int]:
    conn.execute(
        "INSERT INTO topics (topic, category) VALUES ('Excel automation', ?)", (topic_category,)
    )
    topic_id = conn.execute("SELECT id FROM topics").fetchone()["id"]
    conn.execute(
        "INSERT INTO content (topic_id, topic, category, status) "
        "VALUES (?, 'Excel automation', ?, 'PUBLISHED')",
        (topic_id, topic_category),
    )
    content_id = conn.execute("SELECT id FROM content").fetchone()["id"]
    cursor = conn.execute(
        "INSERT INTO product_opportunities (topic_id, proposed_name, price_recommendation, status) "
        "VALUES (?, 'Excel Kit', 19.0, 'APPROVED')",
        (topic_id,),
    )
    conn.commit()
    return cursor.lastrowid, content_id


def test_link_product_creates_active_product_and_attaches_content(migrated_conn):
    opportunity_id, content_id = _insert_approved_opportunity(migrated_conn)
    store = _FakeStore()

    product = link_product(migrated_conn, store, opportunity_id, "excelkit")

    assert product.id is not None
    assert product.status == "ACTIVE"
    assert product.external_product_id == "excelkit"
    assert product.price == 19.0
    assert product.category == "EXCEL"
    assert store.get_product_calls == ["excelkit"]

    content_row = migrated_conn.execute(
        "SELECT product_id FROM content WHERE id = ?", (content_id,)
    ).fetchone()
    assert content_row["product_id"] == product.id


def test_link_product_requires_approved_status(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO product_opportunities (proposed_name, status) VALUES ('X', 'PENDING_APPROVAL')"
    )
    migrated_conn.commit()
    opportunity_id = migrated_conn.execute("SELECT id FROM product_opportunities").fetchone()["id"]

    with pytest.raises(ActivationError):
        link_product(migrated_conn, _FakeStore(), opportunity_id, "excelkit")


def test_link_product_unknown_opportunity_raises(migrated_conn):
    with pytest.raises(ActivationError):
        link_product(migrated_conn, _FakeStore(), 999, "excelkit")


def test_link_product_refuses_to_relink_already_linked_opportunity(migrated_conn):
    opportunity_id, _ = _insert_approved_opportunity(migrated_conn)
    store = _FakeStore()
    link_product(migrated_conn, store, opportunity_id, "excelkit")

    with pytest.raises(ActivationError):
        link_product(migrated_conn, store, opportunity_id, "excelkit")


def test_link_product_propagates_store_error_for_nonexistent_product(migrated_conn):
    from ghostcash.products.base import StoreError

    opportunity_id, _ = _insert_approved_opportunity(migrated_conn)
    store = _FakeStore(error=StoreError("Gumroad API error (HTTP 404): Not Found"))

    with pytest.raises(StoreError):
        link_product(migrated_conn, store, opportunity_id, "nonexistent")


def test_link_product_falls_back_to_opportunity_price_when_store_omits_price(migrated_conn):
    opportunity_id, _ = _insert_approved_opportunity(migrated_conn)
    store = _FakeStore(
        product=StoreProduct(external_id="excelkit", name="Excel Kit", price=None, currency="usd")
    )

    product = link_product(migrated_conn, store, opportunity_id, "excelkit")

    assert product.price == 19.0  # from the opportunity's price_recommendation


def test_link_product_only_attaches_content_without_existing_product(migrated_conn):
    opportunity_id, content_id = _insert_approved_opportunity(migrated_conn)
    # A second content item under the same topic that's ALREADY attached
    # to a different product should be left alone.
    migrated_conn.execute("INSERT INTO products (name) VALUES ('Other product')")
    other_product_id = migrated_conn.execute(
        "SELECT id FROM products WHERE name = 'Other product'"
    ).fetchone()["id"]
    topic_id = migrated_conn.execute(
        "SELECT topic_id FROM content WHERE id = ?", (content_id,)
    ).fetchone()["topic_id"]
    migrated_conn.execute(
        "INSERT INTO content (topic_id, topic, category, status, product_id) "
        "VALUES (?, 'Excel automation', 'EXCEL', 'PUBLISHED', ?)",
        (topic_id, other_product_id),
    )
    migrated_conn.commit()

    store = _FakeStore()
    link_product(migrated_conn, store, opportunity_id, "excelkit")

    untouched = migrated_conn.execute(
        "SELECT product_id FROM content WHERE product_id = ?", (other_product_id,)
    ).fetchone()
    assert untouched["product_id"] == other_product_id  # unchanged
