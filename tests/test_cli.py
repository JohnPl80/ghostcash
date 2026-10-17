from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import ghostcash.cli.commands.generate as generate_cmd
from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.cli.main import build_parser, main
from tests.conftest import FakeAIProvider, sample_content_draft, sample_raw_topic


class _BrokenProvider(AIProvider):
    """AIProvider stub that always fails, for exercising error handling."""

    def generate(self, prompt: str, **kwargs) -> str:
        raise AIProviderError("boom")

    def structured_generate(self, prompt: str, schema: dict) -> dict:
        raise AIProviderError("boom")

    def check_connectivity(self) -> str:
        raise AIProviderError("boom")


def _subcommand_choices(parser: argparse.ArgumentParser) -> dict:
    subparsers_action = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    return subparsers_action.choices


def test_parser_registers_all_claude_md_section_42_commands():
    parser = build_parser()
    command_choices = _subcommand_choices(parser)
    expected = {
        "init",
        "health",
        "generate",
        "render",
        "publish",
        "analytics",
        "optimize",
        "report",
        "run",
    }
    assert expected.issubset(command_choices.keys())


def test_run_command_supports_dry_run_flag():
    parser = build_parser()
    args = parser.parse_args(["run", "--dry-run"])
    assert args.dry_run is True


def test_init_creates_env_config_and_database(isolated_project: Path):
    exit_code = main(["init"])
    assert exit_code == 0
    assert (isolated_project / ".env").exists()
    assert (isolated_project / "config" / "config.yaml").exists()
    assert (isolated_project / "data" / "ghostcash.db").exists()


def test_init_is_safe_to_run_twice(isolated_project: Path):
    assert main(["init"]) == 0
    assert main(["init"]) == 0  # should skip existing files, not fail


def test_health_reports_healthy_after_init(isolated_project: Path):
    main(["init"])
    assert main(["health"]) == 0


def test_health_fails_cleanly_without_init(isolated_project: Path):
    # No config.yaml, no .env, no database - health should fail (exit 1),
    # not crash.
    exit_code = main(["health"])
    assert exit_code == 1


def test_database_commands_fail_cleanly_without_init(isolated_project: Path, capsys):
    # `get_connection()` creates an empty SQLite file if none exists yet
    # (sqlite3's own behavior, not a bug) - without `ghostcash init`
    # these commands used to crash with a raw
    # `sqlite3.OperationalError: no such table: jobs` the moment they
    # tried to record a job, instead of a clean message. Covers every
    # command wrapped by `cli.main._run_against_database`.
    for command in (
        "generate",
        "render",
        "publish",
        "analytics",
        "queue",
        "backup",
        "optimize",
        "run",
    ):
        exit_code = main([command])
        out = capsys.readouterr().out
        assert exit_code == 1, f"`{command}` should fail cleanly, got exit {exit_code}"
        assert "[error]" in out, f"`{command}` printed no clean error: {out!r}"

    exit_code = main(["products", "list"])
    assert exit_code == 1


def test_health_reports_gumroad_api_key_status(isolated_project: Path, monkeypatch, capsys):
    import ghostcash.health as health_module

    main(["init"])

    monkeypatch.delenv("GUMROAD_API_KEY", raising=False)
    main(["health"])
    assert "GUMROAD_API_KEY not set" in capsys.readouterr().out

    # A present key is actually pinged (see tests/test_health.py for full
    # connectivity-check coverage) - mock the store here since this test
    # is only about the CLI wiring, not Gumroad connectivity itself.
    monkeypatch.setenv("GUMROAD_API_KEY", "fake-key")
    fake_store = MagicMock()
    fake_store.get_sales.return_value = None
    monkeypatch.setattr(health_module, "get_store", lambda config: fake_store)
    main(["health"])
    assert "Gumroad API reachable" in capsys.readouterr().out


def test_db_path_override_is_respected(isolated_project: Path):
    custom_db = isolated_project / "custom.db"
    # --db-path should be honored by both init and health, consistently -
    # health correctly reports failure against a path that was never
    # migrated, so we migrate the override path itself here.
    assert main(["--db-path", str(custom_db), "init"]) == 0
    assert custom_db.exists()
    assert main(["--db-path", str(custom_db), "health"]) == 0


def test_generate_fails_cleanly_without_ai_api_key(isolated_project: Path, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    main(["init"])
    assert main(["generate"]) == 1


def test_generate_runs_topics_then_content_and_succeeds(isolated_project: Path, monkeypatch):
    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")

    fake_provider = FakeAIProvider(
        topics_response={"topics": [sample_raw_topic(), sample_raw_topic(topic="idea 2")]},
        content_response=sample_content_draft(),
    )
    monkeypatch.setattr(generate_cmd, "get_ai_provider", lambda ai_config: fake_provider)

    exit_code = main(["generate", "--count", "2", "--content-count", "2"])

    assert exit_code == 0
    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    topic_count = conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0]
    content_count = conn.execute("SELECT COUNT(*) FROM content").fetchone()[0]
    conn.close()
    assert topic_count == 2
    assert content_count == 2


def test_generate_records_both_jobs_as_succeeded(isolated_project: Path, monkeypatch):
    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")
    fake_provider = FakeAIProvider(
        topics_response={"topics": [sample_raw_topic()]},
        content_response=sample_content_draft(),
    )
    monkeypatch.setattr(generate_cmd, "get_ai_provider", lambda ai_config: fake_provider)

    main(["generate"])

    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    rows = dict(
        conn.execute(
            "SELECT job_type, status FROM jobs "
            "WHERE job_type IN ('generate_topics', 'generate_content')"
        ).fetchall()
    )
    conn.close()
    assert rows == {"generate_topics": "SUCCEEDED", "generate_content": "SUCCEEDED"}


def test_generate_succeeds_overall_if_only_one_step_fails(isolated_project: Path, monkeypatch):
    # Topic generation fails (broken provider), but a pre-existing
    # candidate topic lets content generation still do useful work -
    # the overall command should report success.
    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")

    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    conn.execute(
        "INSERT INTO topics (topic, category, overall_score) VALUES (?, ?, ?)",
        ("seed topic", "AI", 0.5),
    )
    conn.commit()
    conn.close()

    class HalfBrokenProvider(AIProvider):
        """Fails only on topic-schema calls; content-schema calls succeed."""

        def __init__(self) -> None:
            self.last_usage = {"prompt_tokens": 1, "output_tokens": 1}

        def generate(self, prompt: str, **kwargs) -> str:
            raise AIProviderError("boom")

        def structured_generate(self, prompt: str, schema: dict) -> dict:
            if "topics" in schema.get("properties", {}):
                raise AIProviderError("boom")
            return sample_content_draft()

        def check_connectivity(self) -> str:
            return "ok"

    monkeypatch.setattr(generate_cmd, "get_ai_provider", lambda ai_config: HalfBrokenProvider())

    exit_code = main(["generate"])

    assert exit_code == 0
    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    rows = dict(
        conn.execute(
            "SELECT job_type, status FROM jobs "
            "WHERE job_type IN ('generate_topics', 'generate_content')"
        ).fetchall()
    )
    conn.close()
    assert rows == {"generate_topics": "FAILED", "generate_content": "SUCCEEDED"}


def test_generate_fails_overall_when_both_steps_fail(isolated_project: Path, monkeypatch):
    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")

    # Seed a candidate topic so content generation has something to
    # attempt (and therefore something that can fail).
    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    conn.execute(
        "INSERT INTO topics (topic, category, overall_score) VALUES (?, ?, ?)",
        ("seed topic", "AI", 0.5),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(generate_cmd, "get_ai_provider", lambda ai_config: _BrokenProvider())

    exit_code = main(["generate"])

    assert exit_code == 1
    conn = sqlite3.connect(isolated_project / "data" / "ghostcash.db")
    rows = dict(
        conn.execute(
            "SELECT job_type, status FROM jobs "
            "WHERE job_type IN ('generate_topics', 'generate_content')"
        ).fetchall()
    )
    conn.close()
    assert rows == {"generate_topics": "FAILED", "generate_content": "FAILED"}


def _insert_approved_content(db_path: Path, **overrides) -> int:
    defaults = dict(
        topic="t",
        category="AI",
        title="title",
        hook="hook",
        script="Short script here.",
        cta="cta",
        content_type="INFORMATION",
        status="APPROVED",
    )
    defaults.update(overrides)
    conn = sqlite3.connect(db_path)
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, hook, script, cta, content_type, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(
            defaults[k]
            for k in (
                "topic",
                "category",
                "title",
                "hook",
                "script",
                "cta",
                "content_type",
                "status",
            )
        ),
    )
    conn.commit()
    row_id = cursor.lastrowid
    conn.close()
    return row_id


def test_render_reports_nothing_to_do_when_no_approved_content(isolated_project: Path):
    main(["init"])
    exit_code = main(["render"])
    assert exit_code == 0


def test_render_renders_approved_content_end_to_end(isolated_project: Path):
    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    _insert_approved_content(db_path)

    exit_code = main(["render", "--count", "1"])

    assert exit_code == 0
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, rendered_video_path FROM content ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    assert row[0] == "READY"
    assert row[1] is not None
    assert Path(row[1]).exists()


def test_render_records_a_job(isolated_project: Path):
    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    _insert_approved_content(db_path)

    main(["render", "--count", "1"])

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT job_type, status FROM jobs WHERE job_type = 'render_videos'"
    ).fetchone()
    conn.close()
    assert row == ("render_videos", "SUCCEEDED")


def test_render_rerenders_videos_missing_from_an_earlier_run(isolated_project: Path, capsys):
    """GitHub runners start with an empty disk: a READY row whose file is
    gone must be rendered again instead of being left stuck."""
    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    content_id = _insert_approved_content(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE content SET status = 'READY', rendered_video_path = ?, "
        "rendered_duration_seconds = 20.0 WHERE id = ?",
        (str(isolated_project / "assets" / "generated" / "vanished.mp4"), content_id),
    )
    conn.commit()
    conn.close()
    capsys.readouterr()

    exit_code = main(["render", "--count", "1"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "1 video file(s) from an earlier run no longer exist" in out
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status, rendered_video_path FROM content WHERE id = ?", (content_id,)
    ).fetchone()
    conn.close()
    assert row[0] == "READY"
    assert row[1] is not None
    assert Path(row[1]).exists()


def test_render_fails_cleanly_with_broken_ffmpeg_binary(isolated_project: Path):
    main(["init"])
    config_path = isolated_project / "config" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nvideo:\n  ffmpeg_binary: /no/such/ffmpeg\n",
        encoding="utf-8",
    )

    exit_code = main(["render"])

    assert exit_code == 1


def test_publish_fails_cleanly_without_buffer_api_key(isolated_project: Path, monkeypatch):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    main(["init"])
    assert main(["publish"]) == 1


def test_publish_reports_no_active_platforms(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.publish as publish_cmd
    from ghostcash.publishing.buffer import BufferPublisher
    from ghostcash.publishing.media_host import MediaHost

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")

    fake_publisher = MagicMock(spec=BufferPublisher)
    fake_publisher.get_organizations.return_value = [{"id": "org1", "name": "Org"}]
    fake_publisher.get_channels.return_value = []  # no channels connected

    class _NullMediaHost(MediaHost):
        def upload(self, file_path, remote_name):
            raise AssertionError("should not be called - nothing to publish")

    monkeypatch.setattr(publish_cmd, "get_publisher", lambda config: fake_publisher)
    monkeypatch.setattr(publish_cmd, "get_media_host", lambda config: _NullMediaHost())

    exit_code = main(["publish"])

    assert exit_code == 0


def test_publish_end_to_end_with_ready_content(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.publish as publish_cmd
    from ghostcash.publishing.base import ScheduleResult
    from ghostcash.publishing.buffer import BufferPublisher
    from ghostcash.publishing.media_host import MediaHost

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")

    db_path = isolated_project / "data" / "ghostcash.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO content (topic, category, title, caption, status, rendered_video_path) "
        "VALUES ('t', 'AI', 'title', 'caption', 'READY', '/tmp/fake.mp4')"
    )
    conn.commit()
    conn.close()

    fake_publisher = MagicMock(spec=BufferPublisher)
    fake_publisher.get_organizations.return_value = [{"id": "org1", "name": "Org"}]
    fake_publisher.get_channels.return_value = [
        {"id": "ch1", "service": "youtube", "isLocked": False, "isDisconnected": False}
    ]
    fake_publisher.schedule.return_value = ScheduleResult(external_post_id="post1", status="DRAFT")

    class _FakeHost(MediaHost):
        def upload(self, file_path, remote_name):
            return "https://example.com/video.mp4"

    monkeypatch.setattr(publish_cmd, "get_publisher", lambda config: fake_publisher)
    monkeypatch.setattr(publish_cmd, "get_media_host", lambda config: _FakeHost())

    exit_code = main(["publish"])

    assert exit_code == 0
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT status FROM published_posts").fetchone()
    content_status = conn.execute("SELECT status FROM content").fetchone()
    conn.close()
    assert row[0] == "DRAFT"
    assert content_status[0] == "SCHEDULED"


def test_publish_records_a_job(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.publish as publish_cmd
    from ghostcash.publishing.buffer import BufferPublisher
    from ghostcash.publishing.media_host import MediaHost

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")

    fake_publisher = MagicMock(spec=BufferPublisher)
    fake_publisher.get_organizations.return_value = [{"id": "org1", "name": "Org"}]
    fake_publisher.get_channels.return_value = []

    class _NullMediaHost(MediaHost):
        def upload(self, file_path, remote_name):
            raise AssertionError("should not be called")

    monkeypatch.setattr(publish_cmd, "get_publisher", lambda config: fake_publisher)
    monkeypatch.setattr(publish_cmd, "get_media_host", lambda config: _NullMediaHost())

    main(["publish"])

    db_path = isolated_project / "data" / "ghostcash.db"
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT job_type, status FROM jobs WHERE job_type = 'publish_content'"
    ).fetchone()
    conn.close()
    assert row == ("publish_content", "SUCCEEDED")


def test_analytics_fails_cleanly_without_buffer_api_key(isolated_project: Path, monkeypatch):
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    main(["init"])
    assert main(["analytics"]) == 1


def test_analytics_end_to_end_collects_metrics_and_evaluates_winners(
    isolated_project: Path, monkeypatch
):
    import ghostcash.cli.commands.analytics as analytics_cmd
    from ghostcash.publishing.buffer import BufferPublisher

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")

    db_path = isolated_project / "data" / "ghostcash.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO content (topic, category, title, status) "
        "VALUES ('t', 'AI', 'title', 'PUBLISHED')"
    )
    conn.execute("INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')")
    conn.execute(
        "INSERT INTO published_posts "
        "(content_id, platform_id, idempotency_key, external_post_id, status) "
        "VALUES (1, 1, 'k1', 'post1', 'PUBLISHED')"
    )
    conn.commit()
    conn.close()

    fake_publisher = MagicMock(spec=BufferPublisher)
    fake_publisher.get_post_status_and_metrics.return_value = {
        "id": "post1",
        "status": "sent",
        "metrics": [
            {"type": "views", "name": "Views", "value": 1000.0, "unit": "count"},
            {"type": "reactions", "name": "Reactions", "value": 200.0, "unit": "count"},
        ],
    }
    monkeypatch.setattr(analytics_cmd, "get_publisher", lambda config: fake_publisher)

    exit_code = main(["analytics"])

    assert exit_code == 0
    conn = sqlite3.connect(db_path)
    perf = conn.execute("SELECT views, likes FROM content_performance").fetchone()
    content_status = conn.execute("SELECT status FROM content WHERE id = 1").fetchone()
    job_row = conn.execute(
        "SELECT job_type, status FROM jobs WHERE job_type = 'collect_analytics'"
    ).fetchone()
    conn.close()
    assert perf == (1000, 200)
    # engagement_rate = 200/1000*100 = 20%, well above the 10% target ->
    # clamps to a full 1.0 engagement component, clearing the default
    # 0.5 Winner Score threshold once redistributed across the only
    # available component.
    assert content_status[0] == "WINNER"
    assert job_row == ("collect_analytics", "SUCCEEDED")


def test_analytics_reports_nothing_to_track(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.analytics as analytics_cmd
    from ghostcash.publishing.buffer import BufferPublisher

    main(["init"])
    monkeypatch.setenv("BUFFER_API_KEY", "fake-key")

    fake_publisher = MagicMock(spec=BufferPublisher)
    monkeypatch.setattr(analytics_cmd, "get_publisher", lambda config: fake_publisher)

    exit_code = main(["analytics"])

    assert exit_code == 0
    fake_publisher.get_post_status_and_metrics.assert_not_called()


# -- products --------------------------------------------------------------


def _seed_qualifying_topic(db_path, category="EXCEL") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO topics (topic, category, target_audience) "
        "VALUES ('Excel automation', ?, 'office workers')",
        (category,),
    )
    topic_id = conn.execute("SELECT id FROM topics").fetchone()[0]
    conn.execute(
        "INSERT INTO content (topic_id, topic, category, status) "
        "VALUES (?, 'Excel automation', ?, 'PUBLISHED')",
        (topic_id, category),
    )
    content_id = conn.execute("SELECT id FROM content").fetchone()[0]
    conn.execute("INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')")
    platform_id = conn.execute("SELECT id FROM platforms").fetchone()[0]
    conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views, likes, comments, shares) "
        "VALUES (?, ?, 70000, 3000, 500, 500)",
        (content_id, platform_id),
    )
    conn.commit()
    conn.close()


def test_products_detect_fails_cleanly_without_ai_api_key(isolated_project: Path, monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    main(["init"])
    assert main(["products", "detect"]) == 1


def test_products_detect_reports_no_qualifying_topics(isolated_project: Path, monkeypatch):
    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key")
    assert main(["products", "detect"]) == 0


def test_products_detect_creates_pending_opportunity(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.products as products_cmd

    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key")
    db_path = isolated_project / "data" / "ghostcash.db"
    _seed_qualifying_topic(db_path)

    fake_ai = FakeAIProvider(
        content_response={
            "proposed_name": "Excel Kit",
            "problem_solved": "Manual Excel work wastes time",
            "contents_outline": ["Templates"],
            "price_recommendation": 19.0,
            "target_customer": "Office workers",
            "sales_page_copy": "Automate your spreadsheets.",
        }
    )
    monkeypatch.setattr(products_cmd, "get_ai_provider", lambda ai_config: fake_ai)

    exit_code = main(["products", "detect"])

    assert exit_code == 0
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT proposed_name, status FROM product_opportunities").fetchone()
    conn.close()
    assert row == ("Excel Kit", "PENDING_APPROVAL")


def test_products_approve_and_reject(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.products as products_cmd

    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key")
    db_path = isolated_project / "data" / "ghostcash.db"
    _seed_qualifying_topic(db_path)

    fake_ai = FakeAIProvider(
        content_response={
            "proposed_name": "Excel Kit",
            "problem_solved": "x",
            "contents_outline": [],
            "price_recommendation": 19.0,
            "target_customer": "y",
            "sales_page_copy": "z",
        }
    )
    monkeypatch.setattr(products_cmd, "get_ai_provider", lambda ai_config: fake_ai)
    main(["products", "detect"])

    conn = sqlite3.connect(db_path)
    opportunity_id = conn.execute("SELECT id FROM product_opportunities").fetchone()[0]
    conn.close()

    assert main(["products", "approve", str(opportunity_id)]) == 0
    conn = sqlite3.connect(db_path)
    status = conn.execute("SELECT status FROM product_opportunities").fetchone()[0]
    conn.close()
    assert status == "APPROVED"


def test_products_link_fails_cleanly_without_gumroad_api_key(isolated_project: Path, monkeypatch):
    monkeypatch.delenv("GUMROAD_API_KEY", raising=False)
    main(["init"])
    assert main(["products", "link", "1", "excelkit"]) == 1


def test_products_link_end_to_end(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.products as products_cmd
    from ghostcash.products.base import StoreProduct
    from ghostcash.products.gumroad import GumroadStore

    main(["init"])
    monkeypatch.setenv("GUMROAD_API_KEY", "fake-key")
    db_path = isolated_project / "data" / "ghostcash.db"

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO topics (topic, category) VALUES ('Excel automation', 'EXCEL')")
    topic_id = conn.execute("SELECT id FROM topics").fetchone()[0]
    conn.execute(
        "INSERT INTO content (topic_id, topic, category, status) "
        "VALUES (?, 'Excel automation', 'EXCEL', 'PUBLISHED')",
        (topic_id,),
    )
    conn.execute(
        "INSERT INTO product_opportunities (topic_id, proposed_name, price_recommendation, status) "
        "VALUES (?, 'Excel Kit', 19.0, 'APPROVED')",
        (topic_id,),
    )
    conn.commit()
    opportunity_id = conn.execute("SELECT id FROM product_opportunities").fetchone()[0]
    conn.close()

    fake_store = MagicMock(spec=GumroadStore)
    fake_store.get_product.return_value = StoreProduct(
        external_id="excelkit",
        name="Excel Kit",
        price=19.0,
        currency="usd",
        url="https://gumroad.com/l/excelkit",
    )
    monkeypatch.setattr(products_cmd, "get_store", lambda config: fake_store)

    exit_code = main(["products", "link", str(opportunity_id), "excelkit"])

    assert exit_code == 0
    conn = sqlite3.connect(db_path)
    product_row = conn.execute("SELECT status, external_product_id FROM products").fetchone()
    conn.close()
    assert product_row == ("ACTIVE", "excelkit")


def test_products_sync_sales_fails_cleanly_without_gumroad_api_key(
    isolated_project: Path, monkeypatch
):
    monkeypatch.delenv("GUMROAD_API_KEY", raising=False)
    main(["init"])
    assert main(["products", "sync-sales"]) == 1


def test_products_sync_sales_end_to_end(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.products as products_cmd
    from ghostcash.products.base import Sale, SalesPage
    from ghostcash.products.gumroad import GumroadStore

    main(["init"])
    monkeypatch.setenv("GUMROAD_API_KEY", "fake-key")
    db_path = isolated_project / "data" / "ghostcash.db"

    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO products (name, external_product_id) VALUES ('P', 'excelkit')")
    product_id = conn.execute("SELECT id FROM products").fetchone()[0]
    conn.execute(
        "INSERT INTO content (topic, category, status, product_id) "
        "VALUES ('t', 'EXCEL', 'PUBLISHED', ?)",
        (product_id,),
    )
    content_id = conn.execute("SELECT id FROM content").fetchone()[0]
    conn.execute("INSERT INTO platforms (name, buffer_channel_id) VALUES ('YOUTUBE', 'ch1')")
    platform_id = conn.execute("SELECT id FROM platforms").fetchone()[0]
    conn.execute(
        "INSERT INTO content_performance (content_id, platform_id, views) VALUES (?, ?, 1000)",
        (content_id, platform_id),
    )
    conn.commit()
    conn.close()

    fake_store = MagicMock(spec=GumroadStore)
    fake_store.get_sales.return_value = SalesPage(
        sales=[
            Sale(
                external_sale_id="sale1",
                external_product_id="excelkit",
                amount=20.0,
                currency="usd",
                customer_email="buyer@example.com",
                status="COMPLETED",
            )
        ],
        next_page_key=None,
    )
    monkeypatch.setattr(products_cmd, "get_store", lambda config: fake_store)

    exit_code = main(["products", "sync-sales"])

    assert exit_code == 0
    conn = sqlite3.connect(db_path)
    order_row = conn.execute("SELECT amount, status FROM orders").fetchone()
    revenue = conn.execute("SELECT revenue FROM content_performance").fetchone()[0]
    conn.close()
    assert order_row == (20.0, "COMPLETED")
    assert revenue == 20.0


def test_report_fails_cleanly_without_init(isolated_project: Path):
    exit_code = main(["report"])
    assert exit_code == 1


def test_report_prints_system_status_and_metrics(isolated_project: Path, capsys):
    main(["init"])

    exit_code = main(["report"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "GhostCash daily report" in out
    assert "SYSTEM STATUS" in out
    assert "Revenue today" in out
    assert "Content published" in out
    assert "Top content" in out


def test_dashboard_command_parses_host_and_port():
    parser = build_parser()
    args = parser.parse_args(["dashboard", "--host", "0.0.0.0", "--port", "9001"])
    assert args.host == "0.0.0.0"
    assert args.port == 9001


def test_dashboard_defaults_to_localhost_8000():
    parser = build_parser()
    args = parser.parse_args(["dashboard"])
    assert args.host == "127.0.0.1"
    assert args.port == 8000


def test_dashboard_fails_cleanly_on_unparseable_config(isolated_project: Path):
    # Not "without init": load_config() falls back to config.example.yaml
    # when config.yaml doesn't exist yet (same as every other command),
    # so a fresh, un-initialized project is not an error case here - only
    # a config that fails to parse at all is.
    (isolated_project / "config" / "config.yaml").write_text(
        "not: valid: yaml: at: all:::", encoding="utf-8"
    )
    exit_code = main(["dashboard"])
    assert exit_code == 1


def test_dashboard_starts_uvicorn_with_the_configured_app(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.dashboard as dashboard_cmd

    main(["init"])
    calls = {}

    def fake_run(app, host, port, log_level):
        calls["host"] = host
        calls["port"] = port
        calls["app"] = app

    monkeypatch.setattr(dashboard_cmd.uvicorn, "run", fake_run)

    exit_code = main(["dashboard", "--port", "9123"])

    assert exit_code == 0
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9123
    assert calls["app"] is not None  # a real FastAPI app, not a stub


def test_backup_fails_cleanly_without_a_database(isolated_project: Path):
    # No `init` yet - nothing to back up.
    exit_code = main(["backup"])
    assert exit_code == 1


def test_backup_creates_a_compressed_snapshot_and_records_a_job(isolated_project: Path, capsys):
    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    _insert_approved_content(db_path)

    exit_code = main(["backup"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[ok] backup written:" in out
    backup_dir = isolated_project / "data" / "backups"
    snapshots = list(backup_dir.glob("ghostcash-*.db.gz"))
    assert len(snapshots) == 1

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT job_type, status FROM jobs WHERE job_type = 'backup_database'"
    ).fetchone()
    conn.close()
    assert row == ("backup_database", "SUCCEEDED")


def test_backup_prunes_on_a_second_run(isolated_project: Path, monkeypatch):
    import ghostcash.cli.commands.backup as backup_cmd
    from ghostcash.backup import run_backup as real_run_backup

    main(["init"])

    # Force every backup onto a distinct calendar day so the retention
    # policy's daily tier actually has to start evicting.
    config_path = isolated_project / "config" / "config.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\nbackup:\n  daily_retention: 2\n",
        encoding="utf-8",
    )

    days = iter(range(1, 6))

    def fake_run_backup(config, db_path, now=None):
        from datetime import UTC, datetime

        return real_run_backup(config, db_path, now=datetime(2026, 10, next(days), tzinfo=UTC))

    monkeypatch.setattr(backup_cmd, "run_backup", fake_run_backup)

    for _ in range(5):
        assert main(["backup"]) == 0

    backup_dir = isolated_project / "data" / "backups"
    snapshots = list(backup_dir.glob("ghostcash-*.db.gz"))
    assert len(snapshots) == 3  # 2 daily + 1 promoted into the single ISO week present


def test_queue_reports_healthy_with_a_full_buffer(isolated_project: Path):
    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    for _ in range(35):  # 35 / 5 per day = 7 days, meets the 7-day target
        _insert_approved_content(db_path, status="READY")

    exit_code = main(["queue"])

    assert exit_code == 0


def test_queue_exits_nonzero_when_below_alert_threshold(isolated_project: Path, capsys):
    main(["init"])  # no content at all -> 0 days remaining
    exit_code = main(["queue"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "[alert]" in out


def test_queue_warns_but_succeeds_when_below_emergency_only(isolated_project: Path, capsys):
    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    for _ in range(10):  # 10 / 5 per day = 2 days: below emergency(3), not below alert(1)
        _insert_approved_content(db_path, status="READY")

    exit_code = main(["queue"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[warn]" in out


def test_queue_writes_github_actions_output_and_summary_files(
    isolated_project: Path, monkeypatch, tmp_path
):
    main(["init"])
    output_file = tmp_path / "github_output.txt"
    summary_file = tmp_path / "github_summary.md"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_file))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    main(["queue"])

    output_text = output_file.read_text(encoding="utf-8")
    assert "below_emergency=true" in output_text
    assert "below_alert=true" in output_text
    assert "ready_count=0" in output_text
    assert "days_remaining=0.00" in output_text
    summary_text = summary_file.read_text(encoding="utf-8")
    assert "Publishing queue" in summary_text


def test_optimize_runs_without_an_ai_key_and_reports_it_was_skipped(
    isolated_project: Path, monkeypatch, capsys
):
    main(["init"])
    monkeypatch.delenv("AI_API_KEY", raising=False)

    exit_code = main(["optimize"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "AI_API_KEY not set" in out
    assert "no data yet" in out


def test_optimize_replicates_winners_when_ai_key_is_configured(
    isolated_project: Path, monkeypatch, capsys
):
    import ghostcash.cli.commands.optimize as optimize_cmd

    main(["init"])
    db_path = isolated_project / "data" / "ghostcash.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO content (topic, category, title, hook, script, cta, content_type, status) "
        "VALUES ('t', 'AI', 'title', 'hook', 'script', 'cta', 'INFORMATION', 'WINNER')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")
    fake_provider = FakeAIProvider(
        content_response={
            "alternative_hooks": ["alt " + str(i) for i in range(5)],
            "alternative_scripts": ["alt script " + str(i) for i in range(5)],
            "alternative_ctas": ["alt cta " + str(i) for i in range(3)],
            "related_topics": ["alt topic " + str(i) for i in range(3)],
        }
    )
    monkeypatch.setattr(optimize_cmd, "get_ai_provider", lambda ai_config: fake_provider)

    exit_code = main(["optimize"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Replicated 1 winner(s)" in out

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT job_type, status FROM jobs WHERE job_type = 'optimize'").fetchone()
    conn.close()
    assert row == ("optimize", "SUCCEEDED")


def test_optimize_skips_cleanly_before_the_interval_has_elapsed(isolated_project: Path, capsys):
    main(["init"])
    assert main(["optimize"]) == 0
    capsys.readouterr()

    exit_code = main(["optimize"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "[skip]" in out


def test_optimize_force_runs_even_within_the_interval(isolated_project: Path, capsys):
    main(["init"])
    main(["optimize"])
    capsys.readouterr()

    exit_code = main(["optimize", "--force"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "--force given - running anyway" in out


def test_run_fails_cleanly_without_ai_api_key(isolated_project: Path, monkeypatch, capsys):
    main(["init"])
    monkeypatch.delenv("AI_API_KEY", raising=False)

    exit_code = main(["run", "--dry-run"])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "[error] missing required environment variable(s): AI_API_KEY" in out


def test_run_dry_run_end_to_end_never_touches_buffer_or_gumroad(
    isolated_project: Path, monkeypatch, capsys
):
    # Three real platforms configured (the example config's default:
    # YOUTUBE, TIKTOK, INSTAGRAM) - dry-run's "posts that WOULD be
    # scheduled" count is every READY item times every configured
    # platform, with no Buffer call involved at all.
    import ghostcash.cli.commands.run as run_cmd

    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")
    # No BUFFER_API_KEY / GUMROAD_API_KEY at all - a dry run must not
    # need them, since it's explicitly section 43's "before Buffer/
    # Gumroad are even set up" scenario.
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    monkeypatch.delenv("GUMROAD_API_KEY", raising=False)

    fake_provider = FakeAIProvider(
        topics_response={"topics": [sample_raw_topic(), sample_raw_topic(topic="idea 2")]},
        content_response=sample_content_draft(),
    )
    monkeypatch.setattr(run_cmd, "get_ai_provider", lambda ai_config: fake_provider)

    exit_code = main(["run", "--dry-run", "--topics-count", "2", "--content-count", "1"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "GhostCash end-to-end run (DRY RUN)" in out
    assert "Topics generated: 2" in out
    assert "Topics selected: 1" in out
    assert "Scripts generated: 1" in out
    assert "Videos rendered: 1" in out
    assert "Posts that WOULD be scheduled: 3" in out  # 1 ready item x 3 platforms
    assert "Products affected: 0" in out
    assert "External API writes: DISABLED" in out
    assert "analytics: skipped in dry run" in out
    assert "GhostCash daily report" in out  # section 62's final "report" step

    db_path = isolated_project / "data" / "ghostcash.db"
    conn = sqlite3.connect(db_path)
    content_row = conn.execute(
        "SELECT status, rendered_video_path FROM content ORDER BY id DESC LIMIT 1"
    ).fetchone()
    job_row = conn.execute(
        "SELECT job_type, status FROM jobs WHERE job_type = 'run_dry_run'"
    ).fetchone()
    published_posts_count = conn.execute("SELECT COUNT(*) FROM published_posts").fetchone()[0]
    conn.close()
    assert content_row[0] == "READY"
    assert content_row[1] is not None and Path(content_row[1]).exists()
    assert job_row == ("run_dry_run", "SUCCEEDED")
    # The whole point of dry-run: nothing was actually scheduled anywhere.
    assert published_posts_count == 0


def test_run_live_skips_publishing_and_analytics_without_buffer_key(
    isolated_project: Path, monkeypatch, capsys
):
    import ghostcash.cli.commands.run as run_cmd

    main(["init"])
    monkeypatch.setenv("AI_API_KEY", "fake-key-for-test")
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)

    fake_provider = FakeAIProvider(
        topics_response={"topics": [sample_raw_topic()]},
        content_response=sample_content_draft(),
    )
    monkeypatch.setattr(run_cmd, "get_ai_provider", lambda ai_config: fake_provider)

    exit_code = main(["run", "--topics-count", "1", "--content-count", "1"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "GhostCash end-to-end run (LIVE)" in out
    assert "publishing skipped - missing environment variable(s): BUFFER_API_KEY" in out
    assert "analytics skipped - missing environment variable(s): BUFFER_API_KEY" in out
    assert "Posts scheduled: 0" in out
    assert "External API writes: ENABLED" in out

    db_path = isolated_project / "data" / "ghostcash.db"
    conn = sqlite3.connect(db_path)
    job_row = conn.execute(
        "SELECT job_type, status FROM jobs WHERE job_type = 'run_live'"
    ).fetchone()
    conn.close()
    assert job_row == ("run_live", "SUCCEEDED")
