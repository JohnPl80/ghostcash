"""`ghostcash stats` - read-only statistics per channel."""

from __future__ import annotations

from ghostcash.analytics.stats import build_stats
from ghostcash.config.schema import Config


def _platform(conn, name: str) -> int:
    row = conn.execute("SELECT id FROM platforms WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO platforms (name, buffer_channel_id) VALUES (?, ?)", (name, name.lower())
    ).lastrowid


def _video(conn, topic: str, category: str, metrics: dict[str, tuple[int, int, int, int]]) -> int:
    topic_id = conn.execute(
        "INSERT INTO topics (topic, category) VALUES (?, ?)", (topic, category)
    ).lastrowid
    content_id = conn.execute(
        "INSERT INTO content (topic_id, topic, category, title, status) "
        "VALUES (?, ?, ?, 't', 'SCHEDULED')",
        (topic_id, topic, category),
    ).lastrowid
    for platform, (views, likes, comments, shares) in metrics.items():
        platform_id = _platform(conn, platform)
        conn.execute(
            "INSERT INTO published_posts (content_id, platform_id, idempotency_key, status, "
            "published_at) VALUES (?, ?, ?, 'PUBLISHED', '2026-11-01T12:00:00Z')",
            (content_id, platform_id, f"{content_id}-{platform}"),
        )
        conn.execute(
            "INSERT INTO content_performance (content_id, platform_id, views, likes, comments, "
            "shares, collected_at) VALUES (?, ?, ?, ?, ?, ?, '2026-11-07T11:20:00+00:00')",
            (content_id, platform_id, views, likes, comments, shares),
        )
    conn.commit()
    return content_id


def test_stats_without_any_data(migrated_conn):
    text = build_stats(migrated_conn, Config())
    assert "CHANNEL: Main channel (Shortcut Club)" in text
    assert "Posts: none recorded yet." in text
    assert "Metrics: none collected yet" in text
    assert "Product opportunities recorded: none" in text


def test_stats_totals_categories_top_and_product_rule(migrated_conn):
    _video(
        migrated_conn,
        "Cancel hidden subscriptions",
        "MONEY_SAVING",
        {"YOUTUBE": (900, 40, 5, 10), "INSTAGRAM": (300, 20, 2, 3)},
    )
    _video(migrated_conn, "Excel shortcut", "EXCEL", {"YOUTUBE": (100, 2, 0, 0)})

    text = build_stats(migrated_conn, Config())

    assert "Metrics last collected: 2026-11-07 11:20 UTC" in text
    # YouTube: 2 videos, 1000 views, 57 engaged -> 5.7%
    lines = text.splitlines()
    totals = lines[lines.index("Totals by platform:") + 1 :]
    youtube = next(line for line in totals if line.strip().startswith("YOUTUBE "))
    assert youtube.split()[1:3] == ["2", "1000"]
    assert "5.7%" in youtube
    # Category ranking by views, MONEY_SAVING first (1200 views).
    first_category = lines[lines.index("By category (all platforms):") + 2]
    assert first_category.split()[:3] == ["MONEY_SAVING", "1", "1200"]
    # Top video adds both platforms together.
    assert "1200 views" in text and "(IN 300, YO 900)" in text
    # 1200 views and 80/1200 = 6.7% engagement -> qualifies under 1000 / 5%.
    assert "Topics that qualify now: 1" in text


def test_stats_names_the_brand(migrated_conn):
    config = Config()
    config.brand.name = "Old Wisdom, New Day"
    assert build_stats(migrated_conn, config).startswith("CHANNEL: Old Wisdom, New Day")


def test_stats_command_runs(isolated_project, capsys):
    from ghostcash.cli.main import main

    assert main(["init"]) == 0
    capsys.readouterr()
    assert main(["stats"]) == 0
    assert "CHANNEL:" in capsys.readouterr().out
