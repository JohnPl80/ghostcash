"""The hook and call to action must be spoken once, not twice.

Seen on the owner's first 50 videos (2026-10-22): the AI wrote the opening
line and the closing line into its "script" as well, and the video then
narrated hook + script + call to action, saying both lines twice.
"""

from __future__ import annotations

from ghostcash.config.schema import Config
from ghostcash.content.generation import _build_prompt, _content_schema, generate_scripts
from ghostcash.content.script_cleanup import strip_repeated_hook_and_cta
from ghostcash.models.content import Content
from ghostcash.models.topic import Topic
from ghostcash.video.renderer import render_content
from tests.conftest import FakeAIProvider, sample_content_draft

BODY = (
    "Open Make and add a Gmail watcher. "
    "Point it at the folder you want in Google Drive. "
    "Every new attachment now saves itself."
)


def test_the_owners_example_loses_the_repeated_opening_and_closing_lines():
    script = f"Stop downloading attachments, instead do this. {BODY} Follow for more."

    cleaned = strip_repeated_hook_and_cta(
        script, "Stop downloading attachments instead do this", "Follow for more."
    )

    assert cleaned == BODY


def test_a_lightly_reworded_hook_and_a_longer_cta_still_count_as_repeats():
    script = f"Stop downloading attachments. Do this instead. {BODY} Follow for more Excel tips."

    cleaned = strip_repeated_hook_and_cta(
        script, "Stop downloading attachments, do this instead", "Follow for more."
    )

    assert cleaned == BODY


def test_a_two_sentence_hook_is_removed_whole():
    script = f"You lose hours every week. Here is why. {BODY}"

    cleaned = strip_repeated_hook_and_cta(script, "You lose hours every week. Here is why.", "")

    assert cleaned == BODY


def test_a_script_without_repeats_is_untouched():
    assert (
        strip_repeated_hook_and_cta(BODY, "Stop downloading attachments", "Follow for more.")
        == BODY
    )


def test_real_content_that_only_shares_words_with_the_hook_is_kept():
    script = (
        "Excel has a shortcut for that. Press Control and Shift together. Then release both keys."
    )

    cleaned = strip_repeated_hook_and_cta(script, "Stop wasting time in Excel", "Follow for more.")

    assert cleaned == script


def test_a_closing_tip_is_not_mistaken_for_the_call_to_action():
    script = f"{BODY} Save yourself an hour every week."

    assert strip_repeated_hook_and_cta(script, "", "Follow for more.") == script


def test_it_never_empties_the_script():
    only_repeats = "Stop doing this. Follow for more."
    assert (
        strip_repeated_hook_and_cta(only_repeats, "Stop doing this", "Follow for more.")
        == only_repeats
    )
    assert (
        strip_repeated_hook_and_cta("Stop doing this.", "Stop doing this", "") == "Stop doing this."
    )


def test_missing_values_are_handled():
    assert strip_repeated_hook_and_cta(None, None, None) == ""
    assert strip_repeated_hook_and_cta("", "hook", "cta") == ""
    assert strip_repeated_hook_and_cta(BODY, None, None) == BODY


# -- wired into generation and rendering ---------------------------------------


def test_generation_stores_the_script_without_the_copies(migrated_conn):
    migrated_conn.execute(
        "INSERT INTO topics (topic, category, overall_score, status) "
        "VALUES ('Excel shortcuts', 'EXCEL', 0.5, 'CANDIDATE')"
    )
    migrated_conn.commit()
    hook = "You are wasting hours in Excel every single week."
    cta = "Follow for more Excel tips."
    provider = FakeAIProvider(
        content_response=sample_content_draft(
            hook=hook, cta=cta, script=f"{hook} {BODY} Follow for more."
        )
    )

    created = generate_scripts(provider, migrated_conn, Config(), count=1)

    assert created[0].script == BODY
    stored = migrated_conn.execute("SELECT script FROM content").fetchone()["script"]
    assert stored == BODY


def test_the_ai_is_told_not_to_repeat_the_hook_or_the_call_to_action():
    properties = _content_schema()["properties"]
    assert "Do NOT repeat the hook" in properties["script"]["description"]
    assert "spoken last" in properties["cta"]["description"]
    prompt = _build_prompt(Topic(topic="t", category="AI", id=1), 90, None, None)
    assert "never repeat the hook" in prompt


class _RecordingVoice:
    cache_identity = None

    def __init__(self) -> None:
        self.texts: list[str] = []

    def synthesize(self, text: str, output_path: str) -> None:
        from ghostcash.video.tts import FliteTTSProvider

        self.texts.append(text)
        FliteTTSProvider().synthesize(text, output_path)


def test_rendering_narrates_the_hook_and_call_to_action_once(tmp_path):
    hook = "Stop downloading attachments instead do this"
    cta = "Follow for more."
    content = Content(
        id=1,
        topic="t",
        category="AI",
        title="t",
        hook=hook,
        script=f"Stop downloading attachments, instead do this. {BODY} Follow for more.",
        caption="c",
        cta=cta,
        content_type="INFORMATION",
        status="APPROVED",
    )
    config = Config()
    config.video.output_dir = str(tmp_path / "videos")
    voice = _RecordingVoice()

    render_content(content, config, tts_provider=voice)

    narration = voice.texts[0].lower()
    assert narration.count("stop downloading attachments") == 1
    assert narration.count("follow for more") == 1
    assert content.script.startswith("Stop downloading")  # the stored row is not altered
