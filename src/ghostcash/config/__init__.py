"""Configuration loading for GhostCash.

Non-secret settings come from config/config.yaml (see config.example.yaml).
Secrets and a handful of hot-path AI settings come from environment
variables / .env and always take precedence over the YAML file
(CLAUDE.md section 10).
"""

from ghostcash.config.loader import ConfigError, load_config, validate_environment
from ghostcash.config.schema import (
    AIConfig,
    BackupConfig,
    Config,
    HumanApprovalMode,
    MonthlyBudget,
    QueueConfig,
    RetryConfig,
    ScoringWeights,
    VideoConfig,
)

__all__ = [
    "AIConfig",
    "BackupConfig",
    "Config",
    "ConfigError",
    "HumanApprovalMode",
    "MonthlyBudget",
    "QueueConfig",
    "RetryConfig",
    "ScoringWeights",
    "VideoConfig",
    "load_config",
    "validate_environment",
]
