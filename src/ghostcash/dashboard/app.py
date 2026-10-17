"""FastAPI dashboard app (CLAUDE.md sections 39-41).

`create_app()` builds a fresh `FastAPI` instance wired to one database
path - tests build their own instance against a throwaway migrated
database rather than sharing global state (see tests/test_dashboard.py).
Each request opens and closes its own `sqlite3.Connection`; this is a
local, single-user, occasionally-checked dashboard (section 39: "do not
build a large frontend"), not a high-concurrency service, so a
per-request connection is simpler and plenty fast - no pool needed.

Auto-generated docs (`/docs`, `/redoc`, `/openapi.json`) are disabled:
every route here returns rendered HTML for a human, not a JSON API, so
Swagger's UI would just be clutter with nothing useful to show.
"""

from __future__ import annotations

import sqlite3

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse

from ghostcash.config.schema import Config
from ghostcash.dashboard import queries, render
from ghostcash.database.connection import get_connection
from ghostcash.health import check_system


def create_app(config: Config, db_path: str | None = None) -> FastAPI:
    resolved_db_path = db_path or config.database_path
    app = FastAPI(title="GhostCash Dashboard", docs_url=None, redoc_url=None)

    def _conn() -> sqlite3.Connection:
        return get_connection(resolved_db_path)

    @app.exception_handler(sqlite3.Error)
    def _database_not_ready(request: Request, exc: sqlite3.Error) -> HTMLResponse:
        # Most likely cause: `ghostcash dashboard` run before `ghostcash
        # init` has ever created/migrated the database. A clear page
        # beats a raw traceback (section 37: every failure observable).
        return HTMLResponse(
            "<h1>Database not ready</h1>"
            "<p>Run <code>ghostcash init</code> to create and migrate it, "
            "then reload this page.</p>"
            f"<p style='color:#888'>{exc}</p>",
            status_code=503,
        )

    @app.get("/", response_class=HTMLResponse)
    def overview() -> str:
        conn = _conn()
        try:
            status = queries.get_system_status(conn, config, resolved_db_path)
            html = render.render_overview(
                status=status,
                revenue=queries.get_revenue_summary(conn),
                orders=queries.get_order_counts(conn),
                content_counts=queries.get_content_counts(conn),
                top_content=queries.get_top_content(conn, limit=10),
                top_categories=queries.get_top_categories(conn, limit=10),
                top_products=queries.get_top_products(conn, limit=10),
                failed_jobs=queries.get_failed_jobs(conn, limit=10),
            )
        finally:
            conn.close()
        return html

    @app.get("/content", response_class=HTMLResponse)
    def content_page(status: str | None = Query(default=None)) -> str:
        conn = _conn()
        try:
            rows = queries.get_content_list(conn, status=status, limit=100)
        finally:
            conn.close()
        return render.render_content(rows, status)

    @app.get("/products", response_class=HTMLResponse)
    def products_page() -> str:
        conn = _conn()
        try:
            opportunities = queries.get_opportunities(conn)
            products = queries.get_products(conn)
        finally:
            conn.close()
        return render.render_products(opportunities, products)

    @app.get("/analytics", response_class=HTMLResponse)
    def analytics_page() -> str:
        conn = _conn()
        try:
            rows = queries.get_content_performance(conn, limit=100)
        finally:
            conn.close()
        return render.render_analytics(rows)

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page() -> str:
        conn = _conn()
        try:
            rows = queries.get_recent_jobs(conn, limit=50)
        finally:
            conn.close()
        return render.render_jobs(rows)

    @app.get("/system", response_class=HTMLResponse)
    def system_page() -> str:
        result = check_system(config, resolved_db_path)
        return render.render_system(result)

    return app
