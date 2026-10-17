from __future__ import annotations

import pytest

from ghostcash.config.loader import ConfigError, load_config, validate_environment


def test_load_config_from_example_file_uses_documented_defaults():
    config = load_config("config/config.example.yaml", load_env_file=False)
    assert config.database_path == "data/ghostcash.db"
    assert config.quality_score_threshold == 80
    assert config.ai.provider == "gemini"
    assert config.scoring_weights.curiosity == pytest.approx(0.20)
    assert config.monthly_budget.total == pytest.approx(15)
    assert config.human_approval_mode.publishing == "APPROVAL_REQUIRED"
    assert config.platforms == ["YOUTUBE", "TIKTOK", "INSTAGRAM"]


def test_load_config_missing_file_raises_config_error(tmp_path):
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError):
        load_config(missing, load_env_file=False)


def test_load_config_rejects_non_mapping_yaml(tmp_path):
    bad = tmp_path / "config.yaml"
    bad.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(bad, load_env_file=False)


def test_env_vars_override_yaml_values(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("database_path: data/from-yaml.db\n", encoding="utf-8")

    monkeypatch.setenv("DATABASE_PATH", "data/from-env.db")
    monkeypatch.setenv("AI_API_KEY", "secret-key")
    monkeypatch.setenv("AI_MAX_REQUESTS_PER_RUN", "42")
    monkeypatch.setenv("AI_TEMPERATURE", "0.25")

    config = load_config(config_path, load_env_file=False)

    assert config.database_path == "data/from-env.db"
    assert config.ai.api_key == "secret-key"
    assert config.ai.max_requests_per_run == 42
    assert isinstance(config.ai.max_requests_per_run, int)
    assert config.ai.temperature == pytest.approx(0.25)
    assert isinstance(config.ai.temperature, float)


def test_unknown_yaml_keys_are_ignored_rather_than_raising(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "database_path: data/x.db\nscoring_weights:\n  curiosity: 0.5\n  made_up_field: 1\n",
        encoding="utf-8",
    )
    config = load_config(config_path, load_env_file=False)
    assert config.scoring_weights.curiosity == pytest.approx(0.5)
    assert not hasattr(config.scoring_weights, "made_up_field")


def test_validate_environment_reports_missing_vars(monkeypatch):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    monkeypatch.delenv("BUFFER_API_KEY", raising=False)
    missing = validate_environment(["ai", "publishing"])
    assert "AI_API_KEY" in missing
    assert "BUFFER_API_KEY" in missing


def test_validate_environment_empty_when_vars_present(monkeypatch):
    monkeypatch.setenv("AI_API_KEY", "x")
    assert validate_environment(["ai"]) == []
