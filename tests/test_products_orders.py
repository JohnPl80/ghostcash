from __future__ import annotations

from ghostcash.products.base import Sale, SalesPage, Store
from ghostcash.products.orders import list_orders, sync_orders, total_revenue_for_product


class _FakeStore(Store):
    def __init__(self, pages: list[SalesPage]):
        self._pages = list(pages)
        self.calls: list[str | None] = []

    def create_product(self, *a, **k):
        raise NotImplementedError

    def update_product(self, *a, **k):
        raise NotImplementedError

    def get_product(self, external_id):
        raise NotImplementedError

    def get_sales(self, after=None, page_key=None):
        self.calls.append(page_key)
        if self._pages:
            return self._pages.pop(0)
        return SalesPage(sales=[], next_page_key=None)


def _seed_product_with_content(conn, external_product_id="ext1", views=1000) -> int:
    cursor = conn.execute(
        "INSERT INTO products (name, external_product_id) VALUES ('P', ?)", (external_product_id,)
    )
    product_id = cursor.lastrowid
    conn.execute(
        "INSERT INTO content (topic, category, status, product_id) "
        "VALUES ('t', 'AI', 'PUBLISHED', ?)",
        (product_id,),
    )
    content_id = conn.execute("SELECT id FROM content").fetchone()["id"]
    platform_id = conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
    ).lastrowid
    conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views) VALUES (?, ?, ?)",
        (content_id, platform_id, views),
    )
    conn.commit()
    return product_id


def _sale(**overrides) -> Sale:
    defaults = dict(
        external_sale_id="sale_1",
        external_product_id="ext1",
        amount=19.0,
        currency="usd",
        customer_email="buyer@example.com",
        status="COMPLETED",
        ordered_at="2026-10-04T00:00:00Z",
    )
    defaults.update(overrides)
    return Sale(**defaults)


# -- sync_orders: recording -----------------------------------------------------


def test_sync_orders_records_a_new_completed_sale(migrated_conn):
    _seed_product_with_content(migrated_conn)
    store = _FakeStore([SalesPage(sales=[_sale()], next_page_key=None)])

    result = sync_orders(store, migrated_conn)

    assert result.sales_seen == 1
    assert result.orders_created == 1
    assert result.orders_updated == 0
    orders = list_orders(migrated_conn)
    assert len(orders) == 1
    assert orders[0].external_sale_id == "sale_1"
    assert orders[0].amount == 19.0
    assert orders[0].status == "COMPLETED"


def test_sync_orders_is_idempotent_on_rerun(migrated_conn):
    _seed_product_with_content(migrated_conn)
    sale = _sale()
    store = _FakeStore([SalesPage(sales=[sale], next_page_key=None)])
    sync_orders(store, migrated_conn)

    store2 = _FakeStore([SalesPage(sales=[sale], next_page_key=None)])
    result2 = sync_orders(store2, migrated_conn)

    assert result2.orders_created == 0
    assert result2.orders_updated == 0
    assert len(list_orders(migrated_conn)) == 1


def test_sync_orders_paginates_through_all_pages(migrated_conn):
    _seed_product_with_content(migrated_conn)
    page1 = SalesPage(sales=[_sale(external_sale_id="s1")], next_page_key="p2")
    page2 = SalesPage(sales=[_sale(external_sale_id="s2")], next_page_key=None)
    store = _FakeStore([page1, page2])

    result = sync_orders(store, migrated_conn)

    assert result.sales_seen == 2
    assert result.orders_created == 2
    assert store.calls == [None, "p2"]


def test_sync_orders_records_order_with_no_matching_product(migrated_conn):
    # Sale for a product GhostCash doesn't know about yet - still
    # recorded faithfully, just with product_id left NULL.
    sale = _sale(external_product_id="unknown")
    store = _FakeStore([SalesPage(sales=[sale], next_page_key=None)])

    result = sync_orders(store, migrated_conn)

    assert result.orders_created == 1
    orders = list_orders(migrated_conn)
    assert orders[0].product_id is None


# -- revenue attribution --------------------------------------------------------


def test_sync_orders_attributes_revenue_to_content_performance(migrated_conn):
    _seed_product_with_content(migrated_conn, views=1000)
    store = _FakeStore([SalesPage(sales=[_sale(amount=50.0)], next_page_key=None)])

    result = sync_orders(store, migrated_conn)

    assert result.revenue_attributed == 50.0
    assert result.revenue_unattributed == 0.0
    perf = migrated_conn.execute(
        "SELECT revenue, revenue_per_1000_views FROM content_performance"
    ).fetchone()
    assert perf["revenue"] == 50.0
    assert perf["revenue_per_1000_views"] == 50.0  # 50 / 1000 views * 1000


def test_sync_orders_leaves_revenue_unattributed_without_views(migrated_conn):
    _seed_product_with_content(migrated_conn, views=0)
    store = _FakeStore([SalesPage(sales=[_sale(amount=50.0)], next_page_key=None)])

    result = sync_orders(store, migrated_conn)

    assert result.revenue_attributed == 0.0
    assert result.revenue_unattributed == 50.0


def test_sync_orders_distributes_revenue_proportionally_by_views(migrated_conn):
    product_id = migrated_conn.execute(
        "INSERT INTO products (name, external_product_id) VALUES ('P', 'ext1')"
    ).lastrowid
    # Two content items promoting the same product with different view counts.
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status, product_id) "
        "VALUES ('a', 'AI', 'PUBLISHED', ?)",
        (product_id,),
    )
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status, product_id) "
        "VALUES ('b', 'AI', 'PUBLISHED', ?)",
        (product_id,),
    )
    rows = migrated_conn.execute("SELECT id FROM content ORDER BY id").fetchall()
    content_a, content_b = rows[0]["id"], rows[1]["id"]
    platform_id = migrated_conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
    ).lastrowid
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views) VALUES (?, ?, 3000)",
        (content_a, platform_id),
    )
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views) VALUES (?, ?, 1000)",
        (content_b, platform_id),
    )
    migrated_conn.commit()

    store = _FakeStore([SalesPage(sales=[_sale(amount=40.0)], next_page_key=None)])
    sync_orders(store, migrated_conn)

    rev_a = migrated_conn.execute(
        "SELECT revenue FROM content_performance WHERE content_id = ?", (content_a,)
    ).fetchone()["revenue"]
    rev_b = migrated_conn.execute(
        "SELECT revenue FROM content_performance WHERE content_id = ?", (content_b,)
    ).fetchone()["revenue"]
    assert rev_a == 30.0  # 3000/4000 * 40
    assert rev_b == 10.0  # 1000/4000 * 40


def test_sync_orders_excludes_rejected_and_failed_content_from_attribution(migrated_conn):
    product_id = migrated_conn.execute(
        "INSERT INTO products (name, external_product_id) VALUES ('P', 'ext1')"
    ).lastrowid
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status, product_id) "
        "VALUES ('a', 'AI', 'PUBLISHED', ?)",
        (product_id,),
    )
    migrated_conn.execute(
        "INSERT INTO content (topic, category, status, product_id) "
        "VALUES ('b', 'AI', 'REJECTED', ?)",
        (product_id,),
    )
    rows = migrated_conn.execute("SELECT id, status FROM content ORDER BY id").fetchall()
    live_id = next(r["id"] for r in rows if r["status"] == "PUBLISHED")
    rejected_id = next(r["id"] for r in rows if r["status"] == "REJECTED")
    platform_id = migrated_conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')"
    ).lastrowid
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views) VALUES (?, ?, 500)",
        (live_id, platform_id),
    )
    migrated_conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views) VALUES (?, ?, 500)",
        (rejected_id, platform_id),
    )
    migrated_conn.commit()

    store = _FakeStore([SalesPage(sales=[_sale(amount=10.0)], next_page_key=None)])
    sync_orders(store, migrated_conn)

    live_revenue = migrated_conn.execute(
        "SELECT revenue FROM content_performance WHERE content_id = ?", (live_id,)
    ).fetchone()["revenue"]
    rejected_revenue = migrated_conn.execute(
        "SELECT revenue FROM content_performance WHERE content_id = ?", (rejected_id,)
    ).fetchone()["revenue"]
    assert live_revenue == 10.0  # gets all of it - rejected content excluded
    assert rejected_revenue == 0.0


def test_sync_orders_reverses_revenue_on_later_refund(migrated_conn):
    _seed_product_with_content(migrated_conn, views=1000)
    completed = _sale(amount=50.0, status="COMPLETED")
    sync_orders(_FakeStore([SalesPage(sales=[completed], next_page_key=None)]), migrated_conn)

    revenue_after_sale = migrated_conn.execute(
        "SELECT revenue FROM content_performance"
    ).fetchone()["revenue"]
    assert revenue_after_sale == 50.0

    refunded = _sale(amount=50.0, status="REFUNDED")
    refunded_store = _FakeStore([SalesPage(sales=[refunded], next_page_key=None)])
    result2 = sync_orders(refunded_store, migrated_conn)

    assert result2.orders_updated == 1
    revenue_after_refund = migrated_conn.execute(
        "SELECT revenue FROM content_performance"
    ).fetchone()["revenue"]
    assert revenue_after_refund == 0.0


def test_total_revenue_for_product_sums_completed_orders_only(migrated_conn):
    product_id = migrated_conn.execute(
        "INSERT INTO products (name, external_product_id) VALUES ('P', 'ext1')"
    ).lastrowid
    migrated_conn.commit()
    store = _FakeStore(
        [
            SalesPage(
                sales=[
                    _sale(external_sale_id="s1", amount=20.0, status="COMPLETED"),
                    _sale(external_sale_id="s2", amount=30.0, status="REFUNDED"),
                    _sale(external_sale_id="s3", amount=15.0, status="COMPLETED"),
                ],
                next_page_key=None,
            )
        ]
    )
    sync_orders(store, migrated_conn)

    assert total_revenue_for_product(migrated_conn, product_id) == 35.0


def test_sync_orders_stops_at_max_pages_safety_valve(migrated_conn):
    # 60 pages, each pointing to the next - should stop at the internal
    # cap rather than looping forever if a store misbehaves.
    pages = [
        SalesPage(sales=[], next_page_key=f"page{i + 1}" if i < 59 else "page_final")
        for i in range(60)
    ]
    store = _FakeStore(pages)

    sync_orders(store, migrated_conn)

    assert len(store.calls) == 50  # _MAX_PAGES
