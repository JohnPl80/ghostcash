"""System health checks (CLAUDE.md sections 36, 41).

Pulled out of `cli/commands/health.py` (where this logic originally
lived alone) so `ghostcash health` and the Phase 9 dashboard's
`/system` page and Overview attention banner run the *exact same*
checks against the *exact same* criteria. Section 41 requires the
dashboard's "do I need to do anything?" banner to be trustworthy - if
it used a second, slightly-different copy of these checks, it could
drift from what `ghostcash health` reports and tell an owner "nothing
required" while the CLI disagrees.

`check_system()` deliberately takes `config` as `Config | None`: a
config that fails to load at all is itself one of the things
`ghostcash health` has always had to report, so the CLI still calls
this even when `load_config()` raised. The dashboard never hits that
case - it cannot have started without a config - but shares the same
function rather than a second one for the common path.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from ghostcash.ai.base import AIProviderError
from ghostcash.ai.factory import get_ai_provider
from ghostcash.config.schema import Config
from ghostcash.database.connection import get_connection
from ghostcash.database.migrate import available_migrations, get_applied_versions
from ghostcash.products.base import StoreError
from ghostcash.products.factory import get_store
from ghostcash.publishing.base import PublisherError
from ghostcash.publishing.buffer import BufferPublisher
from ghostcash.publishing.factory import get_publisher
from ghostcash.video.renderer import build_gemini_tts, flite_required
from ghostcash.video.templates import TEMPLATE_NAMES, TemplateError, load_template
from ghostcash.video.tts import TTSError, is_flite_available

REQUIRED_TABLES: tuple[str, ...] = (
    "content",
    "content_variants",
    "content_performance",
    "topics",
    "topic_scores",
    "products",
    "product_opportunities",
    "campaigns",
    "platforms",
    "published_posts",
    "analytics_snapshots",
    "jobs",
    "job_errors",
    "system_events",
    "settings",
)

MIN_FREE_DISK_MB = 200


@dataclass
class HealthCheckResult:
    notes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        return not self.issues


def check_system(config: Config | None, db_path: str | None) -> HealthCheckResult:
    """Run every health check except config loading itself (the one
    thing only the CLI, which has `args.config_path`, can attempt)."""
    result = HealthCheckResult()

    if db_path is None:
        result.issues.append("no database path configured")
    else:
        try:
            conn = get_connection(db_path)
            try:
                applied = get_applied_versions(conn)
                expected = {p.stem for p in available_migrations()}
                missing_migrations = sorted(expected - applied)
                if missing_migrations:
                    result.issues.append(
                        f"database is missing migrations {missing_migrations}. "
                        "Run `ghostcash init`."
                    )
                else:
                    tables = {
                        row[0]
                        for row in conn.execute(
                            "SELECT name FROM sqlite_master WHERE type = 'table'"
                        ).fetchall()
                    }
                    missing_tables = [t for t in REQUIRED_TABLES if t not in tables]
                    if missing_tables:
                        result.issues.append(f"database missing tables: {missing_tables}")
                    else:
                        result.notes.append("[ok] database schema is up to date")

                    last_job = conn.execute(
                        "SELECT job_type, status, finished_at FROM jobs ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    if last_job is not None:
                        result.notes.append(
                            f"[ok] last job: {last_job['job_type']} ({last_job['status']})"
                        )
                    else:
                        result.notes.append("[info] no jobs have run yet")

                    last_success = conn.execute(
                        "SELECT job_type, finished_at FROM jobs WHERE status = 'SUCCEEDED' "
                        "ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                    max_age_hours = config.max_job_age_hours if config else None
                    if last_success is None:
                        if last_job is not None:
                            # Jobs exist but none has ever succeeded - worth flagging,
                            # distinct from "no jobs have run yet" above.
                            result.issues.append(
                                "no job has ever completed successfully "
                                f"(last attempt: {last_job['job_type']}, {last_job['status']})"
                            )
                    elif max_age_hours is not None:
                        finished_at = datetime.fromisoformat(last_success["finished_at"])
                        age_hours = (datetime.now(UTC) - finished_at).total_seconds() / 3600
                        if age_hours > max_age_hours:
                            result.issues.append(
                                f"last successful job ({last_success['job_type']}) finished "
                                f"{age_hours:.0f}h ago - older than the "
                                f"{max_age_hours}h freshness window. Check that the nightly "
                                "GitHub Actions workflow is actually running on schedule."
                            )
                        else:
                            result.notes.append(
                                f"[ok] last successful job ({last_success['job_type']}) "
                                f"finished {age_hours:.1f}h ago"
                            )
            finally:
                conn.close()
        except Exception as exc:  # sqlite3.Error and friends
            result.issues.append(f"database not accessible at '{db_path}': {exc}")

    _, _, free_bytes = shutil.disk_usage(".")
    free_mb = free_bytes // (1024 * 1024)
    if free_mb < MIN_FREE_DISK_MB:
        result.issues.append(f"low disk space: {free_mb} MB free (want >= {MIN_FREE_DISK_MB} MB)")
    else:
        result.notes.append(f"[ok] disk space: {free_mb} MB free")

    if Path("config/content_categories.yaml").exists():
        result.notes.append(
            "[ok] config/content_categories.yaml present (needed by `ghostcash generate`)"
        )
    else:
        result.issues.append(
            "config/content_categories.yaml is missing - `ghostcash generate` needs it"
        )

    ffmpeg_binary = config.video.ffmpeg_binary if config else "ffmpeg"
    if is_flite_available(ffmpeg_binary):
        result.notes.append("[ok] ffmpeg has libflite support (local TTS for `ghostcash render`)")
    elif config is None or flite_required(config):
        result.issues.append(
            f"ffmpeg ({ffmpeg_binary}) does not support the 'flite' filter needed for "
            "local TTS - the standard Ubuntu/Debian ffmpeg package includes it"
        )

    template_problems = []
    for template_name in TEMPLATE_NAMES:
        try:
            load_template(template_name)
        except TemplateError as exc:
            template_problems.append(str(exc))
    if template_problems:
        result.issues.extend(template_problems)
    else:
        result.notes.append(f"[ok] all {len(TEMPLATE_NAMES)} video templates load correctly")

    if config is not None:
        if config.ai.api_key:
            try:
                provider = get_ai_provider(config.ai)
                confirmation = provider.check_connectivity()
                result.notes.append(f"[ok] AI API ({config.ai.provider}) reachable: {confirmation}")
            except AIProviderError as exc:
                result.issues.append(f"AI API ({config.ai.provider}) not reachable: {exc}")
        else:
            result.notes.append("[info] AI_API_KEY not set - `ghostcash generate` needs it")

        if (config.video.tts_provider or "").strip().lower() == "gemini":
            if not config.ai.api_key:
                result.notes.append(
                    "[warn] video.tts_provider is 'gemini' but AI_API_KEY is not set - "
                    "videos will use the robotic backup voice"
                )
            else:
                try:
                    confirmation = build_gemini_tts(config).check_connectivity()
                    result.notes.append(f"[ok] Gemini voice: {confirmation}")
                except TTSError as exc:
                    # Not an "issue": renders still work through the backup
                    # voice, so this is worth seeing but must not fail the
                    # hourly health check.
                    result.notes.append(f"[warn] Gemini voice not available: {exc}")

        if config.buffer_api_key:
            try:
                publisher = get_publisher(config)
                if isinstance(publisher, BufferPublisher):
                    account = publisher.get_account()
                    label = account.get("name") or account.get("id") or "account"
                    result.notes.append(f"[ok] Buffer API reachable ({label})")
                else:  # pragma: no cover - only one publisher exists today
                    result.notes.append("[ok] BUFFER_API_KEY is set")
            except PublisherError as exc:
                result.issues.append(f"Buffer API not reachable: {exc}")
        else:
            result.notes.append("[info] BUFFER_API_KEY not set - `ghostcash publish` needs it")

        if config.publishing.media_host == "github_release":
            if not config.publishing.github_repo:
                result.notes.append(
                    "[info] publishing.github_repo not set - required for `ghostcash publish` "
                    "to attach videos (media_host: github_release)"
                )
            elif not config.github_token:
                result.notes.append(
                    "[info] GITHUB_TOKEN not set - required for `ghostcash publish` outside "
                    "GitHub Actions (Actions provides one automatically)"
                )
            else:
                result.notes.append("[ok] github_release media host is configured")

        if config.gumroad_api_key:
            try:
                store = get_store(config)
                store.get_sales()
                result.notes.append("[ok] Gumroad API reachable")
            except StoreError as exc:
                result.issues.append(f"Gumroad API not reachable: {exc}")
        else:
            result.notes.append(
                "[info] GUMROAD_API_KEY not set - `ghostcash products link`/`sync-sales` need it"
            )

    return result
