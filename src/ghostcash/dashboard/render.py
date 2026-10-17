"""Hand-rolled HTML rendering for the dashboard (CLAUDE.md section 39:
"FastAPI + simple HTML ... do not build a large frontend").

No template engine (Jinja2 et al.) and no JS/CSS framework - just small
Python functions building strings, the same "cheap and local first"
choice this project already made for ffmpeg filtergraphs and ASS
subtitle files (see video/subtitles.py). Every page shares one
`_page()` shell (nav + a small inline <style> block) so the six routes
in `dashboard/app.py` stay visually consistent without a second
dependency.
"""

from __future__ import annotations

import sqlite3
from html import escape

from ghostcash.dashboard.queries import (
    ContentCounts,
    OrderCounts,
    RevenueSummary,
    SystemStatus,
)
from ghostcash.health import HealthCheckResult

NAV_ITEMS: tuple[tuple[str, str], ...] = (
    ("/", "Overview"),
    ("/content", "Content"),
    ("/products", "Products"),
    ("/analytics", "Analytics"),
    ("/jobs", "Jobs"),
    ("/system", "System"),
)

_STYLE = """
:root { color-scheme: light dark; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial,
    sans-serif;
  margin: 0; padding: 0;
  background: #f6f7f9; color: #1a1a1a;
}
@media (prefers-color-scheme: dark) {
  body { background: #15171a; color: #e7e7e7; }
  .card { background: #1f2227 !important; border-color: #30343a !important; }
  table { background: #1f2227 !important; }
  th { background: #262a30 !important; }
  td, th { border-color: #30343a !important; }
  nav { background: #1a1c20 !important; border-color: #30343a !important; }
  a { color: #7ab8ff !important; }
}
nav {
  background: #fff; border-bottom: 1px solid #e0e0e0; padding: 0.75rem 1.5rem;
  display: flex; gap: 1.25rem; align-items: center;
}
nav strong { margin-right: 0.5rem; }
nav a { text-decoration: none; color: #333; font-size: 0.95rem; }
nav a.active { font-weight: 600; color: #0a66c2; }
main { max-width: 1100px; margin: 0 auto; padding: 1.5rem; }
h1 { font-size: 1.4rem; margin: 0 0 1rem; }
h2 { font-size: 1.05rem; margin: 1.75rem 0 0.5rem; }
.cards { display: flex; flex-wrap: wrap; gap: 1rem; margin-bottom: 1rem; }
.card {
  background: #fff; border: 1px solid #e0e0e0; border-radius: 8px;
  padding: 0.9rem 1.1rem; min-width: 150px; flex: 1;
}
.card .label { font-size: 0.78rem; color: #777; text-transform: uppercase;
  letter-spacing: 0.02em; }
.card .value { font-size: 1.6rem; font-weight: 600; margin-top: 0.15rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; background: #fff; }
th, td { border: 1px solid #e0e0e0; padding: 0.45rem 0.6rem; text-align: left; }
th { background: #fafafa; font-weight: 600; }
.banner { border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1.25rem;
  font-weight: 600; }
.banner.ok { background: #e6f4ea; color: #1e7e34; border: 1px solid #b7e1c4; }
.banner.attention { background: #fdecea; color: #a8321b; border: 1px solid #f5c6bd; }
.banner ol { margin: 0.5rem 0 0; font-weight: 400; }
.muted { color: #888; font-size: 0.85rem; }
.bar-row { display: flex; align-items: center; gap: 0.5rem; margin: 0.25rem 0; }
.bar-label { width: 160px; font-size: 0.85rem; flex-shrink: 0; }
.bar-track { flex: 1; background: #eee; border-radius: 4px; height: 14px; overflow: hidden; }
.bar-fill { background: #0a66c2; height: 100%; }
.bar-value { width: 90px; text-align: right; font-size: 0.85rem; flex-shrink: 0; }
"""


def _page(title: str, active_path: str, body: str) -> str:
    nav_links = "\n".join(
        f'<a href="{href}" class="{"active" if href == active_path else ""}">{label}</a>'
        for href, label in NAV_ITEMS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GhostCash - {escape(title)}</title>
<style>{_STYLE}</style>
</head>
<body>
<nav><strong>GhostCash</strong>{nav_links}</nav>
<main>
<h1>{escape(title)}</h1>
{body}
</main>
</body>
</html>"""


def _table(headers: list[str], rows: list[list[str]], empty_message: str) -> str:
    if not rows:
        return f'<p class="muted">{escape(empty_message)}</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body_rows = "\n".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body_rows}</tbody></table>"


def _fmt_money(value: float) -> str:
    return f"{value:.2f}"


def _fmt_score(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "-"


def _fmt(value: object) -> str:
    return escape(str(value)) if value is not None else "-"


def _status_banner(status: SystemStatus) -> str:
    if status.ok:
        return '<div class="banner ok">&#128994; RUNNING - NOTHING REQUIRED</div>'
    items = "".join(f"<li>{escape(item)}</li>" for item in status.attention_items)
    return f'<div class="banner attention">&#128992; ATTENTION REQUIRED<ol>{items}</ol></div>'


def render_overview(
    *,
    status: SystemStatus,
    revenue: RevenueSummary,
    orders: OrderCounts,
    content_counts: ContentCounts,
    top_content: list[sqlite3.Row],
    top_categories: list[sqlite3.Row],
    top_products: list[sqlite3.Row],
    failed_jobs: list[sqlite3.Row],
) -> str:
    banner = _status_banner(status)

    cards = "".join(
        f'<div class="card"><div class="label">{escape(label)}</div>'
        f'<div class="value">{value}</div></div>'
        for label, value in [
            ("Revenue today", _fmt_money(revenue.today)),
            ("Revenue this week", _fmt_money(revenue.this_week)),
            ("Revenue this month", _fmt_money(revenue.this_month)),
            ("Orders (completed/total)", f"{orders.completed}/{orders.total}"),
            ("Content published", str(content_counts.published)),
            ("Content queued", str(content_counts.queued)),
        ]
    )

    top_content_table = _table(
        ["#", "Topic", "Category", "Status", "Winner Score", "Views"],
        [
            [
                f"#{row['id']}",
                _fmt(row["topic"]),
                _fmt(row["category"]),
                _fmt(row["status"]),
                _fmt_score(row["winner_score"]),
                _fmt(row["views"] or 0),
            ]
            for row in top_content
        ],
        "No content has collected analytics yet.",
    )

    max_revenue = max((row["total_revenue"] or 0 for row in top_categories), default=0)
    if max_revenue > 0:
        category_bars = "".join(
            f'<div class="bar-row"><div class="bar-label">{escape(row["category"])}</div>'
            f'<div class="bar-track"><div class="bar-fill" '
            f'style="width:{(row["total_revenue"] or 0) / max_revenue * 100:.1f}%"></div></div>'
            f'<div class="bar-value">{_fmt_money(row["total_revenue"] or 0)}</div></div>'
            for row in top_categories
        )
        top_categories_html = category_bars or '<p class="muted">No categories yet.</p>'
    else:
        # No revenue anywhere yet - fall back to a views-based bar so
        # the chart is still informative pre-monetization.
        max_views = max((row["total_views"] or 0 for row in top_categories), default=0)
        if max_views > 0:
            top_categories_html = "".join(
                f'<div class="bar-row"><div class="bar-label">{escape(row["category"])}</div>'
                f'<div class="bar-track"><div class="bar-fill" '
                f'style="width:{(row["total_views"] or 0) / max_views * 100:.1f}%"></div></div>'
                f'<div class="bar-value">{_fmt(row["total_views"] or 0)} views</div></div>'
                for row in top_categories
            )
        else:
            top_categories_html = '<p class="muted">No categories with data yet.</p>'

    top_products_table = _table(
        ["Product", "Status", "Price", "Revenue", "Completed orders"],
        [
            [
                _fmt(row["name"]),
                _fmt(row["status"]),
                _fmt_money(row["price"] or 0) if row["price"] is not None else "-",
                _fmt_money(row["total_revenue"] or 0),
                _fmt(row["completed_orders"] or 0),
            ]
            for row in top_products
        ],
        "No products yet.",
    )

    failed_jobs_table = _table(
        ["#", "Job type", "Attempt", "Started", "Error"],
        [
            [
                f"#{row['id']}",
                _fmt(row["job_type"]),
                _fmt(row["attempt"]),
                _fmt(row["started_at"]),
                _fmt((row["error"] or "")[:120]),
            ]
            for row in failed_jobs
        ],
        "No failed jobs.",
    )

    body = f"""
{banner}
<div class="cards">{cards}</div>
<h2>Top 10 content (by Winner Score)</h2>
{top_content_table}
<h2>Top categories (by revenue, else views)</h2>
{top_categories_html}
<h2>Top products</h2>
{top_products_table}
<h2>Failed jobs</h2>
{failed_jobs_table}
"""
    return _page("Overview", "/", body)


def render_content(rows: list[sqlite3.Row], status_filter: str | None) -> str:
    table = _table(
        ["#", "Topic", "Category", "Status", "Quality", "Opportunity", "Created", "Published"],
        [
            [
                f"#{row['id']}",
                _fmt(row["topic"]),
                _fmt(row["category"]),
                _fmt(row["status"]),
                _fmt_score(row["quality_score"]),
                _fmt_score(row["opportunity_score"]),
                _fmt(row["created_at"]),
                _fmt(row["published_at"]),
            ]
            for row in rows
        ],
        "No content matches this filter.",
    )
    filter_note = (
        f'<p class="muted">Filtered by status = {escape(status_filter)}.'
        ' <a href="/content">Clear filter</a></p>'
        if status_filter
        else ""
    )
    body = f"{filter_note}\n{table}"
    return _page("Content", "/content", body)


def render_products(opportunities: list[sqlite3.Row], products: list[sqlite3.Row]) -> str:
    opp_table = _table(
        ["#", "Proposed name", "Status", "Price rec.", "Supporting views", "Created"],
        [
            [
                f"#{row['id']}",
                _fmt(row["proposed_name"]),
                _fmt(row["status"]),
                _fmt_money(row["price_recommendation"] or 0)
                if row["price_recommendation"] is not None
                else "-",
                _fmt(row["supporting_views"] or 0),
                _fmt(row["created_at"]),
            ]
            for row in opportunities
        ],
        "No product opportunities yet - run `ghostcash products detect`.",
    )
    products_table = _table(
        ["#", "Name", "Status", "Price", "Store", "External ID", "Created"],
        [
            [
                f"#{row['id']}",
                _fmt(row["name"]),
                _fmt(row["status"]),
                _fmt_money(row["price"] or 0) if row["price"] is not None else "-",
                _fmt(row["store_provider"]),
                _fmt(row["external_product_id"]),
                _fmt(row["created_at"]),
            ]
            for row in products
        ],
        "No active products yet.",
    )
    body = f"""
<h2>Opportunities</h2>
<p class="muted">Approve with <code>ghostcash products approve &lt;id&gt;</code>, then
create the product on Gumroad and run
<code>ghostcash products link &lt;id&gt; &lt;gumroad_product_id&gt;</code>.</p>
{opp_table}
<h2>Products</h2>
{products_table}
"""
    return _page("Products", "/products", body)


def render_analytics(rows: list[sqlite3.Row]) -> str:
    table = _table(
        [
            "Content",
            "Category",
            "Platform",
            "Views",
            "Engagement",
            "Click rate",
            "Conversion",
            "Revenue",
            "Rev/1000 views",
            "Winner Score",
            "Collected",
        ],
        [
            [
                f"#{row['content_id']} {escape((row['topic'] or '')[:40])}",
                _fmt(row["category"]),
                _fmt(row["platform"]),
                _fmt(row["views"] or 0),
                _fmt_score(row["engagement_rate"]),
                _fmt_score(row["click_rate"]),
                _fmt_score(row["conversion_rate"]),
                _fmt_money(row["revenue"] or 0),
                _fmt_score(row["revenue_per_1000_views"]),
                _fmt_score(row["winner_score"]),
                _fmt(row["collected_at"]),
            ]
            for row in rows
        ],
        "No analytics collected yet - run `ghostcash analytics`.",
    )
    body = (
        f'<p class="muted">"-" means not yet collected (e.g. click rate - Buffer exposes '
        f"no click metric today), never a fabricated zero.</p>\n{table}"
    )
    return _page("Analytics", "/analytics", body)


def render_jobs(rows: list[sqlite3.Row]) -> str:
    table = _table(
        ["#", "Job type", "Status", "Attempt", "Started", "Finished", "Error"],
        [
            [
                f"#{row['id']}",
                _fmt(row["job_type"]),
                _fmt(row["status"]),
                _fmt(row["attempt"]),
                _fmt(row["started_at"]),
                _fmt(row["finished_at"]),
                _fmt((row["error"] or "")[:120]),
            ]
            for row in rows
        ],
        "No jobs recorded yet.",
    )
    return _page("Jobs", "/jobs", table)


def render_system(result: HealthCheckResult) -> str:
    notes_html = "".join(f"<li>{escape(n)}</li>" for n in result.notes) or "<li>(none)</li>"
    issues_html = "".join(f"<li>{escape(i)}</li>" for i in result.issues)
    banner = (
        '<div class="banner ok">&#128994; HEALTHY</div>'
        if result.healthy
        else f'<div class="banner attention">&#128992; ISSUES FOUND<ol>{issues_html}</ol></div>'
    )
    body = f"""
{banner}
<h2>Checks</h2>
<ul>{notes_html}</ul>
<p class="muted">Same checks as <code>ghostcash health</code> - see
ghostcash/health.py.</p>
"""
    return _page("System health", "/system", body)
