"""Typed configuration structures for GhostCash.

Field defaults here mirror config/config.example.yaml, so a Config()
with no arguments is a sane, fully-local, zero-cost default.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AIConfig:
    """CLAUDE.md section 9-10.

    temperature is kept for providers that still use it (OpenAI,
    Anthropic, Ollama, and pre-Gemini-3 models). thinking_level is
    Gemini-3.x-specific: Google deprecated temperature/top_p/top_k for
    that model family in favor of a "low"/"medium"/"high" thinking
    effort control (verified against docs dated 2026-09-02). Other
    providers ignore thinking_level.
    """

    provider: str = "gemini"
    model: str = ""
    api_key: str = ""
    max_requests_per_run: int = 20
    temperature: float = 0.7
    thinking_level: str = "low"


@dataclass
class WinnerWeights:
    """CLAUDE.md section 29 - Winner Score. When a component is
    unavailable for a piece of content (see analytics/metrics.py for why
    click-through and retention often are, via Buffer), its weight is
    redistributed proportionally among whatever components ARE
    available - this generalizes the spec's literal "if revenue data is
    unavailable, redistribute weights automatically" to every component,
    since in practice more than just revenue is often missing before
    Phase 8 (products) exists."""

    engagement: float = 0.25
    click_through: float = 0.25
    revenue: float = 0.35
    retention: float = 0.15


@dataclass
class AnalyticsConfig:
    """CLAUDE.md sections 28-29."""

    winner_weights: WinnerWeights = field(default_factory=WinnerWeights)
    winner_score_threshold: float = 0.5
    # "target" rates are the denominator used to normalize a raw metric
    # into Winner Score's [0, 1] component scale (see
    # analytics/winner.py:normalize_component_value) - a content item
    # hitting the target rate scores 1.0 on that component.
    target_engagement_rate: float = 10.0  # percent
    target_click_rate: float = 5.0  # percent
    target_revenue_per_1000_views: float = 5.0  # same currency as product prices


@dataclass
class PublishingConfig:
    """CLAUDE.md sections 19-22.

    media_host: Buffer's API does not accept file uploads for post
    assets - it needs a stable, public, permanent HTTPS URL (see
    developers.buffer.com/guides/hosting-media.html). "github_release"
    uploads the rendered MP4 as a GitHub Release asset on
    `github_repo` and uses its public download URL - the only option
    that needs no new external account, since GitHub is already
    required (section 54) and GitHub Actions provides a usable token
    automatically. Buffer's own docs suggest Cloudinary or Cloudflare
    R2 as alternatives if you'd rather use one of those.
    """

    provider: str = "buffer"
    # Most Buffer accounts have exactly one organization, auto-detected.
    # Set this explicitly only if your account has more than one.
    organization_id: str = ""
    media_host: str = "github_release"
    github_repo: str = ""  # "owner/repo" - required for github_release
    github_release_tag: str = "ghostcash-media"
    # section 64: first production target is 5 posts/day - used to turn
    # "how much READY/SCHEDULED content is queued" into a days-of-runway
    # estimate for the section 22 queue thresholds.
    posts_per_day: int = 5
    # Buffer rejects a YouTube post without a category. Default 27 =
    # Education; other valid ids are listed in publishing/buffer.py
    # (e.g. 26 Howto & Style, 28 Science & Technology).
    youtube_category_id: str = "27"
    # Send Buffer's isAiGenerated flag (Instagram + YouTube) so the
    # platforms show their AI-content label. GhostCash's scripts are
    # AI-written and the voice is synthetic, so this is on by default;
    # set false only if you have decided the labels are not required.
    disclose_ai_generated: bool = True


@dataclass
class ContentConfig:
    """CLAUDE.md sections 11, 13, 14, 15."""

    # section 11: "at least 20 candidate topics per run unless the
    # configured quota is lower"
    topics_per_run: int = 20

    # section 62 Definition of Done example: 20 topics -> 5 selected -> 5 scripts
    content_per_run: int = 5

    # section 13: default short-form duration 15-35s. ~2.5 words/sec at
    # typical TTS pace puts 35s at ~87 words; rounded up for headroom.
    max_script_words: int = 90

    # section 14: "excessive repetition exists" -> reject if fewer than
    # this fraction of the script's words are distinct.
    min_unique_word_ratio: float = 0.5

    # section 15: reject as a near-duplicate above this lexical
    # similarity ratio (difflib.SequenceMatcher) against recent scripts.
    duplicate_similarity_threshold: float = 0.85

    # section 15: how many recent content rows to compare a new script
    # against. Keeps duplicate detection O(window), not O(all content).
    duplicate_check_window: int = 200


@dataclass
class ScoringWeights:
    """CLAUDE.md section 12 - Opportunity Score weights. Configurable, not permanent."""

    curiosity: float = 0.20
    shareability: float = 0.20
    commercial: float = 0.20
    evergreen: float = 0.15
    usefulness: float = 0.15
    production_simplicity: float = 0.10
    competition_penalty: float = 0.10


@dataclass
class MonthlyBudget:
    """CLAUDE.md section 46 - monthly cost ceilings in EUR."""

    ai: float = 5.0
    video: float = 0.0
    automation: float = 0.0
    total: float = 15.0


@dataclass
class HumanApprovalMode:
    """CLAUDE.md section 52. Values: 'AUTONOMOUS' or 'APPROVAL_REQUIRED'."""

    content: str = "AUTONOMOUS"
    publishing: str = "APPROVAL_REQUIRED"
    products: str = "APPROVAL_REQUIRED"


@dataclass
class QueueConfig:
    """CLAUDE.md section 22 - publishing queue buffer targets, in days."""

    target_days: int = 7
    emergency_threshold_days: int = 3
    alert_threshold_days: int = 1


@dataclass
class RetryConfig:
    """CLAUDE.md section 37 - exponential retry policy for failed jobs."""

    delays_seconds: list[int] = field(default_factory=lambda: [30, 120, 600])
    max_retries: int = 3


@dataclass
class BackupConfig:
    """CLAUDE.md section 58 - SQLite backup retention.

    Backups are compressed copies of the live database taken with
    sqlite3's online backup API (safe against concurrent writers), kept
    outside git (see .gitignore: data/backups/) since they can contain
    customer data (orders.customer_email) that must never land in a
    public repository's permanent history - see
    ghostcash/backup.py and the Phase 10 persistence notes in the
    GitHub Actions workflows for how these survive across ephemeral
    runners instead.
    """

    daily_retention: int = 7
    weekly_retention: int = 4
    backup_dir: str = "data/backups"


@dataclass
class VideoConfig:
    """CLAUDE.md section 16-18 - default render settings."""

    width: int = 1080
    height: int = 1920
    format: str = "mp4"
    codec: str = "h264"
    min_duration_seconds: int = 15
    max_duration_seconds: int = 35

    # section 18: TTS abstraction. "flite" uses ffmpeg's built-in
    # libflite source filter - free, fully local, zero extra
    # dependencies beyond ffmpeg itself, but robotic. "gemini" uses
    # Google's Gemini speech models (natural voice, same AI_API_KEY, free
    # tier) and needs the network.
    tts_provider: str = "flite"
    tts_voice: str = "slt"  # flite voice name
    # Settings for tts_provider "gemini". Voice names: Kore, Puck, Charon,
    # Zephyr, Fenrir, Leda, Aoede, ... (30 in all; try them with
    # `ghostcash voice-test --voice NAME`).
    tts_gemini_model: str = "gemini-3.8-flash-tts"
    tts_gemini_voice: str = "Kore"
    tts_gemini_style: str = "clear, upbeat and friendly, at a brisk natural pace"
    # What to do when the Gemini voice fails (quota, outage): "flite" makes
    # the video with the robotic voice and says so; "none" fails that
    # video, which stays queued and is retried on the next run.
    tts_fallback: str = "flite"

    # section 61 Phase 4 / 62 DoD example: "5 videos rendered" per run.
    render_per_run: int = 5
    output_dir: str = "assets/generated"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_binary: str = "ffprobe"


@dataclass
class ProductsConfig:
    """CLAUDE.md sections 23-27 - product opportunity detection and the
    Gumroad store adapter.

    opportunity_min_views / opportunity_min_engagement_rate: section 25's
    detection signal is "high views + high clicks + low product
    coverage". No GhostCash platform exposes a click metric (see
    analytics/metrics.py), so engagement_rate substitutes for the
    "high clicks" half of that signal here - it's the closest real,
    available measure of audience interest.
    """

    store_provider: str = "gumroad"
    opportunity_min_views: int = 1000
    opportunity_min_engagement_rate: float = 5.0  # percent


@dataclass
class OptimizationConfig:
    """CLAUDE.md sections 30-32, 50-51 - the weekly autonomous
    optimization loop and winner replication.

    `interval_days`: section 50 says "every week" - `ghostcash optimize`
    is safe to invoke more often than that (e.g. daily, from
    nightly.yml), but only does real work once this many days have
    passed since its last successful run (see
    cli/commands/optimize.py), mirroring ghostcash/health.py's
    max_job_age_hours pattern.

    `min_sample_size`: a category/hook-type/template is only used to
    update its learned weight once at least this many measured content
    items support it - below that, one lucky or unlucky video would
    otherwise swing the weight on noise.

    `weight_smoothing_alpha`: the exponential-moving-average blend
    factor for weight updates (0 = never change from the previous
    weight, 1 = fully replace it with this run's observation each
    time). A middling default avoids both staleness and whiplash.

    `max_winners_replicated_per_run`: caps how many winners get a full
    replication pass (hooks/scripts/CTAs/topics - several AI calls'
    worth) in one `ghostcash optimize` run, so a week with many winners
    doesn't blow past AI_MAX_REQUESTS_PER_RUN or the monthly AI budget
    (section 46) in a single invocation. Remaining winners are simply
    picked up on the next run - nothing is lost (winner_replicated_at
    is only set once a winner is actually replicated).

    `min_views_for_experiment_evaluation` / `experiment_timeout_days` /
    `practical_significance_threshold`: section 51's experiments are
    judged by practical margin, not real statistical-significance
    testing - this project's content volume is far too low for that to
    be meaningful. A variant needs at least this many views on both
    sides of the comparison before being judged at all; if that never
    happens within `experiment_timeout_days`, the experiment is closed
    out as INCONCLUSIVE rather than left pending forever. Otherwise, the
    side that beats the other by at least `practical_significance_threshold`
    (a relative fraction) wins.
    """

    interval_days: int = 7
    min_sample_size: int = 5
    weight_smoothing_alpha: float = 0.3
    max_winners_replicated_per_run: int = 3
    min_views_for_experiment_evaluation: int = 200
    experiment_timeout_days: int = 21
    practical_significance_threshold: float = 0.10


@dataclass
class Config:
    """Top-level GhostCash configuration."""

    database_path: str = "data/ghostcash.db"
    quality_score_threshold: int = 80
    # CLAUDE.md section 36: "last successful job recent" is a named health
    # check. Nightly's cron runs once/day, so 36h gives a full day of
    # slack for a late/retried run before `ghostcash health` flags it.
    max_job_age_hours: int = 36

    ai: AIConfig = field(default_factory=AIConfig)
    content: ContentConfig = field(default_factory=ContentConfig)
    publishing: PublishingConfig = field(default_factory=PublishingConfig)
    analytics: AnalyticsConfig = field(default_factory=AnalyticsConfig)
    products: ProductsConfig = field(default_factory=ProductsConfig)
    scoring_weights: ScoringWeights = field(default_factory=ScoringWeights)
    monthly_budget: MonthlyBudget = field(default_factory=MonthlyBudget)
    human_approval_mode: HumanApprovalMode = field(default_factory=HumanApprovalMode)
    queue: QueueConfig = field(default_factory=QueueConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    video: VideoConfig = field(default_factory=VideoConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)

    # Secrets - normally left empty in YAML and supplied via environment
    # variables instead (see config/loader.py ENV_OVERRIDES).
    buffer_api_key: str = ""
    gumroad_api_key: str = ""
    # For publishing.media_host = "github_release". In GitHub Actions this
    # is unnecessary - the workflow's automatic GITHUB_TOKEN is used
    # instead. Only needed for local/manual `ghostcash publish` runs.
    github_token: str = ""
    notification_provider: str = ""
    notification_token: str = ""

    # NOTE: Buffer's public API does not currently support creating posts
    # for TikTok (verified against developers.buffer.com/guides/posts-and-
    # scheduling.html - supported platforms are Instagram, Threads,
    # LinkedIn, X, Facebook, Google Business Profiles, Mastodon, YouTube,
    # Pinterest, Bluesky). TikTok is left in this default list because
    # CLAUDE.md section 21 asks for it and a future publisher may support
    # it; `ghostcash publish` skips it with a clear message today rather
    # than silently dropping it or pretending it works, per section 21's
    # own "continue operating if one channel fails" resilience model.
    platforms: list[str] = field(default_factory=lambda: ["YOUTUBE", "TIKTOK", "INSTAGRAM"])
