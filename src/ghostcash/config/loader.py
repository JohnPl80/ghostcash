"""Load GhostCash configuration from YAML + environment variables.

Precedence (lowest to highest):
  1. dataclass defaults in ghostcash.config.schema
  2. config/config.yaml (falls back to config/config.example.yaml if the
     real file has not been created yet)
  3. .env file in the current working directory
  4. real process environment variables

Secrets should never live in config.yaml - only in the environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ghostcash.config.schema import (
    AIConfig,
    AnalyticsConfig,
    BackupConfig,
    BrandConfig,
    Config,
    ContentConfig,
    HumanApprovalMode,
    MonthlyBudget,
    OptimizationConfig,
    ProductsConfig,
    PublishingConfig,
    QueueConfig,
    RetryConfig,
    ScoringWeights,
    VideoConfig,
    WinnerWeights,
)


class ConfigError(Exception):
    """Raised when configuration cannot be loaded or is invalid."""


DEFAULT_CONFIG_PATH = Path("config/config.yaml")
EXAMPLE_CONFIG_PATH = Path("config/config.example.yaml")

# Maps environment variable name -> dotted attribute path on Config.
# Kept in sync with CLAUDE.md sections 10 and 55.
ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "AI_PROVIDER": ("ai", "provider"),
    "AI_MODEL": ("ai", "model"),
    "AI_API_KEY": ("ai", "api_key"),
    "AI_MAX_REQUESTS_PER_RUN": ("ai", "max_requests_per_run"),
    "AI_TEMPERATURE": ("ai", "temperature"),
    "BUFFER_API_KEY": ("buffer_api_key",),
    "GUMROAD_API_KEY": ("gumroad_api_key",),
    "GITHUB_TOKEN": ("github_token",),
    "NOTIFICATION_PROVIDER": ("notification_provider",),
    "NOTIFICATION_TOKEN": ("notification_token",),
    "DATABASE_PATH": ("database_path",),
}

# Environment variables required per feature area, used by `ghostcash health`
# and by individual commands once they exist. Deliberately NOT enforced at
# import time - only when a command that needs a given feature runs.
# GITHUB_TOKEN is intentionally not required here: GitHub Actions provides
# one automatically, so only local/manual runs need to set it themselves
# (checked separately where the chosen media_host actually needs it).
REQUIRED_ENV_BY_FEATURE: dict[str, list[str]] = {
    "ai": ["AI_API_KEY"],
    "publishing": ["BUFFER_API_KEY"],
    "products": ["GUMROAD_API_KEY"],
}


def _load_dotenv(path: Path) -> None:
    """Minimal `.env` loader (KEY=VALUE per line).

    Deliberately hand-rolled instead of adding a dependency: the format
    needed here is trivial and this keeps the dependency footprint at
    just PyYAML (CLAUDE.md section 4 - avoid unnecessary dependencies).
    Existing environment variables always win (setdefault).
    """
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _resolve_config_path(explicit_path: str | Path | None) -> Path:
    if explicit_path is not None:
        return Path(explicit_path)
    env_path = os.environ.get("GHOSTCASH_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return EXAMPLE_CONFIG_PATH


def _merge(dataclass_cls: type, overrides: dict[str, Any]) -> Any:
    """Build a dataclass instance from its defaults plus a dict of overrides,
    ignoring unknown keys so extra/experimental YAML fields don't crash."""
    base = dataclass_cls()
    known_fields = base.__dict__.keys()
    filtered = {k: v for k, v in overrides.items() if k in known_fields}
    return dataclass_cls(**{**base.__dict__, **filtered})


def _build_analytics_config(raw: dict[str, Any]) -> AnalyticsConfig:
    # AnalyticsConfig nests WinnerWeights, so it needs its own merge
    # instead of the generic _merge() helper above (which would hand the
    # raw winner_weights dict straight to the dataclass constructor in
    # place of a WinnerWeights instance).
    defaults = AnalyticsConfig()
    return AnalyticsConfig(
        winner_weights=_merge(WinnerWeights, raw.get("winner_weights") or {}),
        winner_score_threshold=raw.get("winner_score_threshold", defaults.winner_score_threshold),
        target_engagement_rate=raw.get("target_engagement_rate", defaults.target_engagement_rate),
        target_click_rate=raw.get("target_click_rate", defaults.target_click_rate),
        target_revenue_per_1000_views=raw.get(
            "target_revenue_per_1000_views", defaults.target_revenue_per_1000_views
        ),
    )


def _build_config(raw: dict[str, Any]) -> Config:
    defaults = Config()
    return Config(
        database_path=raw.get("database_path", defaults.database_path),
        quality_score_threshold=raw.get(
            "quality_score_threshold", defaults.quality_score_threshold
        ),
        max_job_age_hours=raw.get("max_job_age_hours", defaults.max_job_age_hours),
        ai=_merge(AIConfig, raw.get("ai") or {}),
        content=_merge(ContentConfig, raw.get("content") or {}),
        publishing=_merge(PublishingConfig, raw.get("publishing") or {}),
        analytics=_build_analytics_config(raw.get("analytics") or {}),
        products=_merge(ProductsConfig, raw.get("products") or {}),
        scoring_weights=_merge(ScoringWeights, raw.get("scoring_weights") or {}),
        monthly_budget=_merge(MonthlyBudget, raw.get("monthly_budget") or {}),
        human_approval_mode=_merge(HumanApprovalMode, raw.get("human_approval_mode") or {}),
        queue=_merge(QueueConfig, raw.get("queue") or {}),
        retry=_merge(RetryConfig, raw.get("retry") or {}),
        backup=_merge(BackupConfig, raw.get("backup") or {}),
        video=_merge(VideoConfig, raw.get("video") or {}),
        optimization=_merge(OptimizationConfig, raw.get("optimization") or {}),
        brand=_merge(BrandConfig, raw.get("brand") or {}),
        buffer_api_key=raw.get("buffer_api_key", ""),
        gumroad_api_key=raw.get("gumroad_api_key", ""),
        notification_provider=raw.get("notification_provider", ""),
        notification_token=raw.get("notification_token", ""),
        platforms=raw.get("platforms", defaults.platforms),
    )


def _coerce_like(reference: Any, value: str) -> Any:
    """Coerce a raw string env var value to match the type of the field it overrides."""
    if isinstance(reference, bool):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    if isinstance(reference, int):
        return int(value)
    if isinstance(reference, float):
        return float(value)
    return value


def _apply_env_overrides(config: Config) -> None:
    for env_key, attr_path in ENV_OVERRIDES.items():
        value = os.environ.get(env_key)
        if not value:
            continue
        target: Any = config
        for attr in attr_path[:-1]:
            target = getattr(target, attr)
        current = getattr(target, attr_path[-1])
        setattr(target, attr_path[-1], _coerce_like(current, value))

    # A brand with its own Buffer account reads ONLY its own variable - never
    # BUFFER_API_KEY - so a missing key means "do not publish", not "publish
    # to the other channel's account".
    key_env = (config.brand.buffer_api_key_env or "BUFFER_API_KEY").strip()
    if key_env != "BUFFER_API_KEY":
        config.buffer_api_key = os.environ.get(key_env, "")


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml_mapping(config_path: Path, seen: tuple[Path, ...] = ()) -> dict[str, Any]:
    """Read a config file. A file may start from another one with
    `extends: <path relative to this file>`: everything in the other file
    applies, and this file's own values win (nested sections are merged key
    by key). A brand's file uses this to share the main settings."""
    resolved = config_path.resolve()
    if resolved in seen:
        raise ConfigError(f"'{config_path}' extends itself (loop through {seen[-1]}).")
    if not config_path.exists():
        raise ConfigError(f"Configuration file '{config_path}' does not exist.")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse '{config_path}': {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"'{config_path}' must contain a YAML mapping at the top level.")

    parent = raw.pop("extends", None)
    if not parent:
        return raw
    parent_path = config_path.parent / str(parent)
    base = _read_yaml_mapping(parent_path, (*seen, resolved))
    return _deep_merge(base, raw)


def load_config(path: str | Path | None = None, load_env_file: bool = True) -> Config:
    """Load and return the effective GhostCash configuration.

    Raises ConfigError if no config file can be found or it fails to parse.
    """
    if load_env_file:
        _load_dotenv(Path(".env"))

    config_path = _resolve_config_path(path)
    if not config_path.exists():
        raise ConfigError(
            f"No configuration file found at '{config_path}'. "
            "Copy config/config.example.yaml to config/config.yaml, "
            "or run `ghostcash init`."
        )

    raw = _read_yaml_mapping(config_path)
    config = _build_config(raw)
    _apply_env_overrides(config)
    # A brand's own database path always wins over DATABASE_PATH: that
    # variable (set in .env, which `ghostcash init` creates) names the MAIN
    # channel's database, and letting it through would mix two channels'
    # content, queue and learning in one file.
    if config.brand.key and raw.get("database_path"):
        config.database_path = str(raw["database_path"])
    return config


def validate_environment(features: list[str]) -> list[str]:
    """Return the list of required environment variables missing for the
    given feature names (see REQUIRED_ENV_BY_FEATURE). Empty list = OK."""
    missing: list[str] = []
    for feature in features:
        for var in REQUIRED_ENV_BY_FEATURE.get(feature, []):
            if not os.environ.get(var):
                missing.append(var)
    return missing
