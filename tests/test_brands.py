"""Several channels ("brands") - each its own config file, database and
Buffer account. See config/brands/ and BrandConfig in config/schema.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.config.schema import Config
from ghostcash.content.generation import _build_prompt as script_prompt
from ghostcash.content.quality import evaluate_content
from ghostcash.content.topics import generate_topics, load_categories
from ghostcash.models.content import Content
from ghostcash.models.topic import Topic
from ghostcash.publishing.pipeline import media_asset_name
from ghostcash.video.templates import TEMPLATE_NAMES, load_template
from tests.conftest import FakeAIProvider, sample_raw_topic

REPO_ROOT = Path(__file__).resolve().parent.parent
OLDWISDOM = REPO_ROOT / "config" / "brands" / "oldwisdom.yaml"


@pytest.fixture()
def no_buffer_env(monkeypatch):
    for name in (
        "BUFFER_API_KEY",
        "BUFFER_API_KEY_OLDWISDOM",
        "GHOSTCASH_CONFIG_PATH",
        "DATABASE_PATH",
    ):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------
# `extends:` - a brand file starts from the main config
# ---------------------------------------------------------------------


def test_extends_merges_nested_sections_and_child_wins(tmp_path, no_buffer_env):
    (tmp_path / "base.yaml").write_text(
        "ai:\n  model: base-model\n  temperature: 0.3\n"
        "publishing:\n  posts_per_day: 5\n  github_repo: owner/repo\n",
        encoding="utf-8",
    )
    child = tmp_path / "brands" / "child.yaml"
    child.parent.mkdir()
    child.write_text("extends: ../base.yaml\npublishing:\n  posts_per_day: 2\n", encoding="utf-8")

    config = load_config(child, load_env_file=False)

    assert config.publishing.posts_per_day == 2  # child wins
    assert config.publishing.github_repo == "owner/repo"  # kept from base
    assert config.ai.model == "base-model"


def test_extends_loop_is_a_clear_error(tmp_path, no_buffer_env):
    (tmp_path / "a.yaml").write_text("extends: b.yaml\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("extends: a.yaml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="extends itself"):
        load_config(tmp_path / "a.yaml", load_env_file=False)


def test_extends_missing_parent_is_a_clear_error(tmp_path, no_buffer_env):
    (tmp_path / "a.yaml").write_text("extends: nowhere.yaml\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="does not exist"):
        load_config(tmp_path / "a.yaml", load_env_file=False)


# ---------------------------------------------------------------------
# Buffer key isolation
# ---------------------------------------------------------------------


def test_brand_never_falls_back_to_the_main_buffer_key(monkeypatch, no_buffer_env):
    monkeypatch.setenv("BUFFER_API_KEY", "shortcut-club-key")
    config = load_config(OLDWISDOM, load_env_file=False)
    assert config.buffer_api_key == ""


def test_brand_reads_its_own_buffer_key(monkeypatch, no_buffer_env):
    monkeypatch.setenv("BUFFER_API_KEY", "shortcut-club-key")
    monkeypatch.setenv("BUFFER_API_KEY_OLDWISDOM", "oldwisdom-key")
    config = load_config(OLDWISDOM, load_env_file=False)
    assert config.buffer_api_key == "oldwisdom-key"


def test_brand_database_path_ignores_database_path_env(monkeypatch, no_buffer_env):
    # .env (created by `ghostcash init` from .env.example) sets DATABASE_PATH
    # to the main channel's database. A brand must still use its own.
    monkeypatch.setenv("DATABASE_PATH", "data/ghostcash.db")
    config = load_config(OLDWISDOM, load_env_file=False)
    assert config.database_path == "data/backups/brands/oldwisdom/ghostcash.db"
    main = load_config(REPO_ROOT / "config" / "config.example.yaml", load_env_file=False)
    assert main.database_path == "data/ghostcash.db"


def test_main_config_is_unchanged_by_brand_support(monkeypatch, no_buffer_env):
    monkeypatch.setenv("BUFFER_API_KEY", "shortcut-club-key")
    main = load_config(REPO_ROOT / "config" / "config.example.yaml", load_env_file=False)
    assert main.buffer_api_key == "shortcut-club-key"
    assert main.brand.key == ""
    assert main.database_path == "data/ghostcash.db"


# ---------------------------------------------------------------------
# The shipped Old Wisdom, New Day config
# ---------------------------------------------------------------------


def test_oldwisdom_config_keeps_everything_in_cached_folders(no_buffer_env):
    # The workflows keep only data/ghostcash.db and data/backups between
    # runs (tests/test_workflow_cache.py). A brand's database, backups and
    # saved narration must live inside data/backups or they are lost.
    config = load_config(OLDWISDOM, load_env_file=False)
    for path in (config.database_path, config.backup.backup_dir, config.video.audio_cache_dir):
        assert path.startswith("data/backups/brands/oldwisdom/"), path
    assert config.video.output_dir != Config().video.output_dir


def test_oldwisdom_config_values(no_buffer_env):
    config = load_config(OLDWISDOM, load_env_file=False)
    assert config.brand.key == "oldwisdom"
    assert config.brand.name == "Old Wisdom, New Day"
    assert config.brand.buffer_api_key_env == "BUFFER_API_KEY_OLDWISDOM"
    assert config.video.tts_gemini_voice == "Charon"  # male narrator
    # New channel: drafts first, whatever Shortcut Club's setting is.
    assert config.human_approval_mode.publishing == "APPROVAL_REQUIRED"
    assert config.content.required_mentions
    assert "cure" in config.content.forbidden_phrases
    # Inherited from the main config.
    main = load_config(REPO_ROOT / "config" / "config.example.yaml", load_env_file=False)
    assert config.ai.model == main.ai.model
    assert config.publishing.github_repo == main.publishing.github_repo


def test_oldwisdom_templates_and_categories_load(no_buffer_env):
    config = load_config(OLDWISDOM, load_env_file=False)
    for name in TEMPLATE_NAMES:
        template = load_template(name, REPO_ROOT / config.brand.templates_dir)
        assert template.font_family == "DejaVu Serif"
    categories = load_categories(REPO_ROOT / config.brand.categories_file)
    assert "CALM" in categories and "EXCEL" not in categories


# ---------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------


def test_topic_prompt_names_the_brand_and_uses_its_categories(migrated_conn, tmp_path):
    categories = tmp_path / "cats.yaml"
    categories.write_text(
        "categories:\n  - key: CALM\n    label: Calm\n  - key: EXCEL\n    label: Excel\n",
        encoding="utf-8",
    )
    config = Config()
    config.brand.name = "Old Wisdom, New Day"
    config.brand.description = "classic quotes for modern days"
    config.brand.categories_file = str(categories)
    provider = FakeAIProvider({"topics": [sample_raw_topic()]})

    generate_topics(provider, migrated_conn, config, count=3)

    prompt, schema = provider.structured_calls[0]
    assert '"Old Wisdom, New Day" (classic quotes for modern days)' in prompt
    assert schema["properties"]["topics"]["items"]["properties"]["category"]["enum"] == [
        "CALM",
        "EXCEL",
    ]


def test_topic_prompt_is_generic_without_a_brand(migrated_conn, tmp_path):
    categories = tmp_path / "cats.yaml"
    categories.write_text("categories:\n  - key: EXCEL\n    label: Excel\n", encoding="utf-8")
    provider = FakeAIProvider({"topics": [sample_raw_topic()]})
    generate_topics(provider, migrated_conn, Config(), count=3, categories_path=categories)
    prompt, _ = provider.structured_calls[0]
    assert "for a faceless content channel (" in prompt


def test_script_prompt_carries_channel_rules():
    topic = Topic(topic="Why worry wastes the day", category="CALM", hook="")
    prompt = script_prompt(
        topic,
        90,
        None,
        None,
        "Ask only to follow.",
        "Old Wisdom, New Day",
        "classic quotes",
        "Build the video around ONE quote.",
    )
    assert 'for the channel "Old Wisdom, New Day" (classic quotes).' in prompt
    assert "Channel rules: Build the video around ONE quote." in prompt
    assert prompt.index("Channel rules") < prompt.index("Call to action")


def test_script_prompt_without_brand_has_no_channel_text():
    topic = Topic(topic="Excel trick", category="EXCEL", hook="")
    prompt = script_prompt(topic, 90, None, None, "")
    assert "for the channel" not in prompt and "Channel rules" not in prompt


# ---------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------

QUOTE_SCRIPT = (
    "Seneca wrote that we suffer more often in imagination than in reality. "
    "Most of what you fear tonight will never happen. Write the worry down, "
    "then write the one thing you can actually do about it tomorrow morning."
)


def _quote_content(**overrides) -> Content:
    values = dict(
        topic="Worry",
        category="CALM",
        hook="Most of your fears never happen.",
        script=QUOTE_SCRIPT,
        caption="An old idea for a restless night.",
        cta="Follow for a new idea every day.",
    )
    values.update(overrides)
    return Content(**values)


def _brand_config() -> Config:
    config = Config()
    config.content.required_mentions = ["Seneca", "Marcus Aurelius"]
    config.content.forbidden_phrases = ["cure", "studies show"]
    return config


def test_script_naming_an_allowed_author_passes(migrated_conn):
    result = evaluate_content(_quote_content(), migrated_conn, _brand_config())
    assert result.passed, result.reasons


def test_script_naming_no_allowed_author_is_rejected(migrated_conn):
    content = _quote_content(script=QUOTE_SCRIPT.replace("Seneca", "A wise man"))
    result = evaluate_content(content, migrated_conn, _brand_config())
    assert not result.passed
    assert any("required sources" in reason for reason in result.reasons)


def test_forbidden_phrase_anywhere_is_rejected(migrated_conn):
    content = _quote_content(caption="This old idea will cure your stress.")
    result = evaluate_content(content, migrated_conn, _brand_config())
    assert not result.passed
    assert any("'cure'" in reason for reason in result.reasons)


def test_forbidden_phrase_matches_whole_words_only(migrated_conn):
    # "secure" contains "cure" but is not the word "cure".
    content = _quote_content(caption="Feel secure in an uncertain week.")
    result = evaluate_content(content, migrated_conn, _brand_config())
    assert result.passed, result.reasons


def test_default_config_has_no_brand_gates(migrated_conn):
    content = _quote_content(script=QUOTE_SCRIPT.replace("Seneca", "A wise man"))
    assert evaluate_content(content, migrated_conn, Config()).passed


# ---------------------------------------------------------------------
# Media host names
# ---------------------------------------------------------------------


def test_media_asset_name_carries_brand_prefix(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"video")
    plain = media_asset_name(7, str(video))
    branded = media_asset_name(7, str(video), "oldwisdom")
    assert plain.startswith("content_7_")
    assert branded == "oldwisdom_" + plain
