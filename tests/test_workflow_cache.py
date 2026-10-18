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
    assert names == {"analytics.yml", "healthcheck.yml", "nightly.yml", "publishing.yml"}


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
    assert len(restores) == 4
    for name, index, steps, step, _ in restores:
        assert step.get("id") == "restore_db", name
        warning = steps[index + 1]
        assert "restore_db.outputs.cache-matched-key == ''" in warning["if"], name
        assert "NO SAVED DATABASE FOUND" in warning["run"], name


def test_saved_narration_audio_lives_inside_a_cached_folder():
    audio_dir = Path(Config().video.audio_cache_dir)
    cached_folders = [Path(path) for path in EXPECTED_CACHE_PATHS]
    assert any(folder in audio_dir.parents for folder in cached_folders), audio_dir
