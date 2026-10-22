"""Guards the GitHub Actions saved-database setup.

GitHub ties every saved cache entry to the exact list of folders it was
saved with. On 2026-10-18 a `data/audio` folder was added to that list and
the very next run silently started from an EMPTY database (content numbered
from #1 again, queue at 0) because every earlier save had become invisible.
These tests make sure that cannot happen again unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from ghostcash.config.schema import Config

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / ".github" / "workflows"
EXPECTED_CACHE_PATHS = ["data/ghostcash.db", "data/backups"]


def _cache_steps():
    found = []
    for workflow_file in sorted(WORKFLOWS_DIR.glob("*.yml")):
        document = yaml.safe_load(workflow_file.read_text(encoding="utf-8"))
        for job in document["jobs"].values():
            steps = job["steps"]
            for index, step in enumerate(steps):
                uses = step.get("uses", "")
                if uses.startswith("actions/cache"):
                    paths = [
                        line.strip() for line in step["with"]["path"].splitlines() if line.strip()
                    ]
                    found.append((workflow_file.name, index, steps, step, paths))
    return found


def test_there_are_cache_steps_in_every_workflow():
    names = {name for name, *_ in _cache_steps()}
    assert names == {
        "analytics.yml",
        "discard-drafts.yml",
        "healthcheck.yml",
        "nightly.yml",
        "publishing.yml",
    }


def test_every_cache_step_uses_exactly_the_original_folder_list():
    for name, _, _, step, paths in _cache_steps():
        assert paths == EXPECTED_CACHE_PATHS, (
            f"{name}: {step['uses']} lists {paths}. Changing this list makes GitHub ignore "
            "every earlier saved database - put anything new INSIDE data/backups instead."
        )


def test_every_restore_is_followed_by_a_no_saved_database_warning():
    restores = [
        item for item in _cache_steps() if item[3]["uses"].startswith("actions/cache/restore")
    ]
    assert len(restores) == 5
    for name, index, steps, step, _ in restores:
        assert step.get("id") == "restore_db", name
        warning = steps[index + 1]
        assert "restore_db.outputs.cache-matched-key == ''" in warning["if"], name
        assert "NO SAVED DATABASE FOUND" in warning["run"], name


def test_saved_narration_audio_lives_inside_a_cached_folder():
    audio_dir = Path(Config().video.audio_cache_dir)
    cached_folders = [Path(path) for path in EXPECTED_CACHE_PATHS]
    assert any(folder in audio_dir.parents for folder in cached_folders), audio_dir


# -- the nightly run must not keep writing videos into a full queue ---------


def _nightly_steps():
    document = yaml.safe_load((WORKFLOWS_DIR / "nightly.yml").read_text(encoding="utf-8"))
    return {
        step["name"]: step
        for job in document["jobs"].values()
        for step in job["steps"]
        if "name" in step
    }


def test_nightly_generation_runs_only_while_the_queue_is_below_target():
    steps = _nightly_steps()
    queue_step = next(
        step for name, step in steps.items() if name.startswith("3. Check publishing queue")
    )
    assert queue_step["id"] == "queue_start"
    assert queue_step["continue-on-error"] is True  # a low queue exits non-zero by design

    generate = steps["6-9. Generate topics, generate content, quality control"]
    assert "queue_start.outputs.below_target != 'false'" in generate["if"]  # unknown -> generate

    skipped = next(step for name, step in steps.items() if name.startswith("6-9 skipped"))
    assert "queue_start.outputs.below_target == 'false'" in skipped["if"]


def test_nightly_rendering_is_never_gated_on_the_queue():
    """Approved scripts waiting for their video count towards the queue, so
    gating the render would leave a full queue of unrendered scripts."""
    render = _nightly_steps()["10. Render approved content to video"]
    assert "below_target" not in render.get("if", "")


# -- the manual "discard drafts" button must not delete by accident ---------


def _discard_workflow():
    return yaml.safe_load((WORKFLOWS_DIR / "discard-drafts.yml").read_text(encoding="utf-8"))


def _discard_step():
    document = _discard_workflow()
    steps = [step for job in document["jobs"].values() for step in job["steps"]]
    (step,) = [step for step in steps if "ghostcash drafts discard" in step.get("run", "")]
    return step


def _discard_triggers():
    document = _discard_workflow()
    return document.get("on", document.get(True))  # PyYAML reads a bare `on` as True


def test_discard_workflow_only_runs_when_started_by_hand():
    assert set(_discard_triggers()) == {"workflow_dispatch"}


def test_discard_workflow_shares_the_database_lock_with_the_other_workflows():
    assert _discard_workflow()["concurrency"]["group"] == "ghostcash-db-state"


def test_discard_workflow_previews_unless_the_word_discard_is_typed():
    script = _discard_step()["run"]
    # --apply is added in exactly one place, and only for the exact word.
    assert script.count("--apply") == 1
    apply_line = script.index("--apply")
    assert script.rindex('[ "$CONFIRM" = "DISCARD" ]', 0, apply_line) != -1
    # Anything else typed in "confirm" stops the run instead of being ignored.
    assert "confirm must be empty" in script


def test_discard_workflow_takes_its_inputs_through_the_environment_only():
    step = _discard_step()
    assert "${{" not in step["run"], "inputs pasted into the script text could inject commands"
    assert step["env"]["CONFIRM"] == "${{ inputs.confirm }}"
    assert step["env"]["HOW_MANY"] == "${{ inputs.how_many }}"
    assert step["env"]["BUFFER_API_KEY"] == "${{ secrets.BUFFER_API_KEY }}"


def test_discard_workflow_defaults_to_one_video_and_no_confirmation():
    inputs = _discard_triggers()["workflow_dispatch"]["inputs"]
    assert inputs["how_many"]["default"] == "1"
    assert inputs["confirm"]["default"] == ""
