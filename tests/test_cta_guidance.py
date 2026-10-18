"""The AI must not promise things the channel doesn't have (a link in the
bio, comment-for-a-link, free downloads) - seen in the first live videos."""

from __future__ import annotations

from typing import Any

import pytest

from ghostcash.ai.base import AIProvider
from ghostcash.config.loader import load_config
from ghostcash.config.schema import Config
from ghostcash.content.generation import generate_draft_for_topic
from ghostcash.content.quality import evaluate_content
from ghostcash.models.content import Content
from ghostcash.models.topic import Topic
from ghostcash.optimization.replication import _build_prompt as replication_prompt
from tests.conftest import sample_content_draft
from tests.test_content_quality import GOOD_SCRIPT


class _RecordingProvider(AIProvider):
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def generate(self, prompt: str, **kwargs: Any) -> str:
        return ""

    def structured_generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.prompts.append(prompt)
        return sample_content_draft()

    def check_connectivity(self) -> str:
        return "ok"


def _content(**overrides) -> Content:
    defaults = dict(
        topic="Excel shortcuts",
        category="EXCEL",
        hook="You are wasting hours in Excel every week.",
        script=GOOD_SCRIPT,
        caption="The shortcut nobody teaches you.",
        cta="Follow for more Excel tips.",
    )
    defaults.update(overrides)
    return Content(**defaults)


# -- the instruction given to the AI ---------------------------------------


def test_default_guidance_only_allows_follow_save_share():
    guidance = Config().content.cta_guidance.lower()
    assert "follow" in guidance and "save" in guidance and "share" in guidance
    assert "link in bio" in guidance  # named as something NOT to say


def test_generation_prompt_carries_the_guidance(migrated_conn):
    provider = _RecordingProvider()
    generate_draft_for_topic(
        provider, Topic(topic="t", category="EXCEL", id=1), Config(), migrated_conn
    )
    assert "Call to action: " + Config().content.cta_guidance in provider.prompts[0]


def test_generation_prompt_uses_custom_guidance_and_omits_it_when_empty(migrated_conn):
    topic = Topic(topic="t", category="EXCEL", id=1)
    config = Config()
    config.content.cta_guidance = "Tell viewers the free checklist is linked in the bio."
    provider = _RecordingProvider()
    generate_draft_for_topic(provider, topic, config, migrated_conn)
    assert "free checklist is linked in the bio" in provider.prompts[0]

    config.content.cta_guidance = ""
    generate_draft_for_topic(provider, topic, config, migrated_conn)
    assert "Call to action:" not in provider.prompts[1]


def test_replication_prompt_carries_the_guidance():
    winner = _content(status="WINNER")
    assert "For the calls to action: Only follow." in replication_prompt(winner, "Only follow.")
    assert "For the calls to action" not in replication_prompt(winner, "")


def test_guidance_can_be_set_from_config_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        'content:\n  cta_guidance: "Only ask for a follow."\n  reject_unavailable_offers: false\n',
        encoding="utf-8",
    )
    config = load_config(str(path))
    assert config.content.cta_guidance == "Only ask for a follow."
    assert config.content.reject_unavailable_offers is False


# -- the safety net -------------------------------------------------------

UNAVAILABLE = [
    ("cta", "Visit the link in bio to get the full guide."),
    ("cta", "Link in my bio for the template."),
    ("cta", "Comment 'card' to send a link."),
    ("cta", "Comment CARD and I'll DM you the guide."),
    ("cta", "DM me for the spreadsheet."),
    ("cta", "Download my free Excel template today."),
    ("cta", "Grab our budget planner bundle."),
    ("caption", "Full list in the link below."),
    ("caption", "The link in the description has everything."),
    ("script", "That's why the link is in the bio."),
]

FINE = [
    "Follow for more Excel tips.",
    "Save this for later and share it with a friend.",
    "Comment which shortcut you want next.",
    "Try the HYPERLINK function in Excel.",
    "Use the link between two cells in Power Query.",
    "Message your boss before you send the file.",
    "Download the update from Microsoft's site.",
]


@pytest.mark.parametrize(("field", "text"), UNAVAILABLE)
def test_offers_that_do_not_exist_are_rejected(migrated_conn, field, text):
    result = evaluate_content(_content(**{field: text}), migrated_conn, Config())
    assert result.passed is False
    assert result.quality_score == 0
    assert any("does not exist yet" in reason for reason in result.reasons)


@pytest.mark.parametrize("text", FINE)
def test_ordinary_calls_to_action_are_not_rejected(migrated_conn, text):
    result = evaluate_content(_content(cta=text), migrated_conn, Config())
    assert result.passed is True, result.reasons


def test_the_check_can_be_switched_off_once_the_offer_is_real(migrated_conn):
    config = Config()
    config.content.reject_unavailable_offers = False
    result = evaluate_content(_content(cta="Visit the link in bio."), migrated_conn, config)
    assert result.passed is True
