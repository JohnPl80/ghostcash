"""A plain-text statistics overview of one channel (`ghostcash stats`).

Read-only: it only reads what the daily analytics job has already stored
(content_performance holds the latest views/likes/comments/shares of every
post) and never contacts Buffer or any other service.

Engagement here is (likes + comments + shares) / views, the same definition
the product-opportunity rule uses (products/opportunities.py), so the
"product rule" section shows exactly how far each topic is from triggering a
product proposal.
"""

from __future__ import annotations

import sqlite3
from statistics import median

from ghostcash.config.schema import Config


def _pct(engaged: int, views: int) -> str:
    return f"{engaged / views * 100:.1f}%" if views else "-"


def _date(value: str | None) -> str:
    return (value or "")[:10] or "-"


def _channel_label(config: Config) -> str:
    return config.brand.name or "Main channel (Shortcut Club)"


def build_stats(conn: sqlite3.Connection, config: Config, top: int = 10) -> str:
    lines: list[str] = []
    out = lines.append

    out(f"CHANNEL: {_channel_label(config)}")
    out("=" * 60)

    posts = conn.execute(
        """
        SELECT p.name AS platform, pp.status AS status, COUNT(*) AS n,
               MIN(pp.published_at) AS first_at, MAX(pp.published_at) AS last_at
        FROM published_posts pp JOIN platforms p ON p.id = pp.platform_id
        GROUP BY p.name, pp.status ORDER BY p.name, pp.status
        """
    ).fetchall()
    if posts:
        out("Posts by platform and status (GhostCash's records):")
        for row in posts:
            span = ""
            if row["status"] == "PUBLISHED":
                span = f"  (first {_date(row['first_at'])}, latest {_date(row['last_at'])})"
            out(f"  {row['platform']:<10} {row['status']:<10} {row['n']:>4}{span}")
    else:
        out("Posts: none recorded yet.")

    perf = conn.execute(
        """
        SELECT cp.content_id AS content_id, p.name AS platform,
               cp.views AS views, cp.likes AS likes, cp.comments AS comments,
               cp.shares AS shares, cp.collected_at AS collected_at,
               c.topic AS topic, c.category AS category, c.topic_id AS topic_id
        FROM content_performance cp
        JOIN content c ON c.id = cp.content_id
        LEFT JOIN platforms p ON p.id = cp.platform_id
        """
    ).fetchall()

    out("")
    if not perf:
        out("Metrics: none collected yet (the daily Analytics run fills them in).")
        _append_opportunities(conn, out)
        return "\n".join(lines)

    latest = max((row["collected_at"] or "") for row in perf)
    out(f"Metrics last collected: {latest[:16].replace('T', ' ')} UTC")
    out("Engagement = (likes + comments + shares) / views.")

    out("")
    out("Totals by platform:")
    out(
        f"  {'platform':<10} {'videos':>6} {'views':>8} {'likes':>6} {'comm.':>6} "
        f"{'shares':>6} {'engag.':>7} {'median':>7} {'best':>7}"
    )
    by_platform: dict[str, list[sqlite3.Row]] = {}
    for row in perf:
        by_platform.setdefault(row["platform"] or "?", []).append(row)
    for platform, rows in sorted(by_platform.items()):
        views = sum(r["views"] for r in rows)
        engaged = sum(r["likes"] + r["comments"] + r["shares"] for r in rows)
        per_video = [r["views"] for r in rows]
        out(
            f"  {platform:<10} {len(rows):>6} {views:>8} {sum(r['likes'] for r in rows):>6} "
            f"{sum(r['comments'] for r in rows):>6} {sum(r['shares'] for r in rows):>6} "
            f"{_pct(engaged, views):>7} {median(per_video):>7.0f} {max(per_video):>7}"
        )

    # One entry per video (platforms added together).
    videos: dict[int, dict] = {}
    for row in perf:
        video = videos.setdefault(
            row["content_id"],
            {
                "topic": row["topic"],
                "category": row["category"],
                "topic_id": row["topic_id"],
                "views": 0,
                "engaged": 0,
                "by_platform": {},
            },
        )
        video["views"] += row["views"]
        video["engaged"] += row["likes"] + row["comments"] + row["shares"]
        video["by_platform"][row["platform"] or "?"] = row["views"]

    out("")
    out("By category (all platforms):")
    out(f"  {'category':<16} {'videos':>6} {'views':>8} {'per video':>9} {'engag.':>7}")
    categories: dict[str, list[dict]] = {}
    for video in videos.values():
        categories.setdefault(video["category"] or "?", []).append(video)
    for category, items in sorted(
        categories.items(), key=lambda kv: -sum(v["views"] for v in kv[1])
    ):
        views = sum(v["views"] for v in items)
        engaged = sum(v["engaged"] for v in items)
        out(
            f"  {category:<16} {len(items):>6} {views:>8} {views / len(items):>9.0f} "
            f"{_pct(engaged, views):>7}"
        )

    out("")
    out(f"Top {top} videos by views (all platforms):")
    ranked = sorted(videos.items(), key=lambda kv: -kv[1]["views"])[:top]
    for content_id, video in ranked:
        split = ", ".join(f"{p[:2]} {v}" for p, v in sorted(video["by_platform"].items()))
        topic = (video["topic"] or "")[:55]
        rate = _pct(video["engaged"], video["views"])
        out(
            f"  #{content_id:<4} {video['views']:>6} views {rate:>6}"
            f"  [{video['category']}] {topic} ({split})"
        )

    out("")
    min_views = config.products.opportunity_min_views
    min_rate = config.products.opportunity_min_engagement_rate
    out(
        f"Product rule: a topic needs >= {min_views} views and >= {min_rate:g}% engagement "
        "(all its videos and platforms together)."
    )
    topics: dict[int, dict] = {}
    for video in videos.values():
        if video["topic_id"] is None:
            continue
        entry = topics.setdefault(
            video["topic_id"], {"topic": video["topic"], "views": 0, "engaged": 0}
        )
        entry["views"] += video["views"]
        entry["engaged"] += video["engaged"]
    qualifying = [
        t
        for t in topics.values()
        if t["views"] >= min_views and t["views"] and t["engaged"] / t["views"] * 100 >= min_rate
    ]
    out(f"  Topics that qualify now: {len(qualifying)}")
    closest = sorted(topics.values(), key=lambda t: -t["views"])[:5]
    if closest:
        out("  Closest by views:")
        for t in closest:
            rate = _pct(t["engaged"], t["views"])
            out(f"    {t['views']:>6} views {rate:>6}  {(t['topic'] or '')[:60]}")

    _append_opportunities(conn, out)
    return "\n".join(lines)


def _append_opportunities(conn: sqlite3.Connection, out) -> None:
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM product_opportunities GROUP BY status"
    ).fetchall()
    summary = ", ".join(f"{r['status']} {r['n']}" for r in rows) or "none"
    out("")
    out(f"Product opportunities recorded: {summary}")
