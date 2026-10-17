from __future__ import annotations

import json
import subprocess

import pytest

from ghostcash.config.schema import Config
from ghostcash.models.content import Content
from ghostcash.video.renderer import (
    RenderError,
    Scene,
    _allocate_durations,
    _build_scenes,
    _count_available_segments,
    render_batch,
    render_content,
    select_approved_content,
)
from ghostcash.video.templates import load_template


def _approved_content(**overrides) -> Content:
    defaults = dict(
        topic="Excel shortcuts",
        category="EXCEL",
        title="5 Excel tricks",
        hook="You waste hours in Excel.",
        script="Point one is short. Point two is also short.",
        caption="cap",
        cta="Follow now.",
        content_type="INFORMATION",
        status="APPROVED",
    )
    defaults.update(overrides)
    return Content(**defaults)


# ---------------------------------------------------------------------
# Pure logic (no ffmpeg) - fast
# ---------------------------------------------------------------------


def test_count_available_segments_prefers_visual_sequence():
    content = _approved_content(
        visual_sequence_json=json.dumps(["a", "b", "c", "d"]),
        script="Only one sentence here",
    )
    assert _count_available_segments(content) == 4


def test_count_available_segments_falls_back_to_sentences():
    content = _approved_content(visual_sequence_json=None, script="One. Two. Three.")
    assert _count_available_segments(content) == 3


def test_build_scenes_information_shape():
    content = _approved_content(
        content_type="INFORMATION",
        visual_sequence_json=json.dumps(["point a", "point b"]),
    )
    template = load_template("INFORMATION")
    scenes = _build_scenes(content, template)

    assert [s.slot for s in scenes] == ["HOOK", "POINT_1", "POINT_2", "CTA"]
    assert scenes[0].text == content.hook
    assert scenes[-1].text == content.cta
    assert scenes[-1].background_color == template.cta_background_color


def test_build_scenes_list_numbers_points():
    content = _approved_content(
        content_type="LIST", visual_sequence_json=json.dumps(["first", "second"])
    )
    template = load_template("LIST")
    scenes = _build_scenes(content, template)

    point_scenes = [s for s in scenes if s.slot.startswith("POINT_")]
    assert point_scenes[0].text == "1. first"
    assert point_scenes[1].text == "2. second"


def test_build_scenes_problem_solution_is_always_four_scenes():
    content = _approved_content(
        content_type="PROBLEM_SOLUTION",
        script="Why it happens sentence. Solution sentence here.",
    )
    template = load_template("PROBLEM_SOLUTION")
    scenes = _build_scenes(content, template)

    assert [s.slot for s in scenes] == ["PROBLEM", "WHY_IT_HAPPENS", "SOLUTION", "CTA"]


def test_build_scenes_falls_back_when_no_visual_sequence():
    content = _approved_content(
        content_type="INFORMATION",
        visual_sequence_json=None,
        script="First point here. Second point here. Third point here.",
    )
    template = load_template("INFORMATION")
    scenes = _build_scenes(content, template)
    point_scenes = [s for s in scenes if s.slot.startswith("POINT_")]
    assert template.points_min <= len(point_scenes) <= template.points_max
    assert all(s.text.strip() for s in scenes)


def test_allocate_durations_sums_to_total():
    scenes = [Scene("A", "short", "#000"), Scene("B", "a much longer piece of text here", "#111")]
    _allocate_durations(scenes, total_duration=10.0, min_scene_seconds=1.0)
    assert sum(s.duration_seconds for s in scenes) == pytest.approx(10.0)


def test_allocate_durations_respects_minimum():
    scenes = [Scene("A", "x", "#000"), Scene("B", "y" * 500, "#111")]
    _allocate_durations(scenes, total_duration=5.0, min_scene_seconds=2.0)
    assert scenes[0].duration_seconds >= 2.0


# ---------------------------------------------------------------------
# Real ffmpeg integration - slower, kept to short scripts
# ---------------------------------------------------------------------


def _probe(path, entries):
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_render_content_produces_valid_mp4(tmp_path):
    config = Config()
    config.video.output_dir = str(tmp_path)
    content = _approved_content(id=1)

    result = render_content(content, config)

    assert result.output_path.endswith("content_1.mp4")
    probe = _probe(result.output_path, "stream=codec_name,codec_type:format=duration")
    codecs = {s["codec_type"]: s["codec_name"] for s in probe["streams"]}
    assert codecs["video"] == "h264"
    assert codecs["audio"] == "aac"
    assert float(probe["format"]["duration"]) > 0
    assert result.duration_seconds > 0
    assert len(result.scenes) >= 3  # HOOK + at least 1 point + CTA


def test_render_content_output_resolution_matches_config(tmp_path):
    config = Config()
    config.video.output_dir = str(tmp_path)
    content = _approved_content(id=2)

    result = render_content(content, config)

    probe = _probe(result.output_path, "stream=width,height")
    video_stream = next(s for s in probe["streams"] if "width" in s)
    assert video_stream["width"] == 1080
    assert video_stream["height"] == 1920


def test_render_content_raises_for_empty_script(tmp_path):
    config = Config()
    config.video.output_dir = str(tmp_path)
    content = _approved_content(script="")
    with pytest.raises(RenderError, match="no script"):
        render_content(content, config)


def test_render_content_raises_for_missing_content_type(tmp_path):
    config = Config()
    config.video.output_dir = str(tmp_path)
    content = _approved_content(content_type=None)
    with pytest.raises(RenderError, match="content_type"):
        render_content(content, config)


def test_render_content_raises_for_unknown_ffmpeg_binary(tmp_path):
    config = Config()
    config.video.output_dir = str(tmp_path)
    config.video.ffmpeg_binary = "/no/such/ffmpeg"
    content = _approved_content(id=3)
    with pytest.raises(RenderError):
        render_content(content, config)


# ---------------------------------------------------------------------
# Batch orchestration
# ---------------------------------------------------------------------


def _insert_approved_content(conn, **overrides) -> int:
    defaults = dict(
        topic="t",
        category="AI",
        title="title",
        hook="hook",
        script="Short script here.",
        cta="cta",
        content_type="INFORMATION",
        status="APPROVED",
    )
    defaults.update(overrides)
    cursor = conn.execute(
        "INSERT INTO content (topic, category, title, hook, script, cta, content_type, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(
            defaults[k]
            for k in (
                "topic",
                "category",
                "title",
                "hook",
                "script",
                "cta",
                "content_type",
                "status",
            )
        ),
    )
    conn.commit()
    return cursor.lastrowid


def test_select_approved_content_excludes_already_rendered(migrated_conn):
    _insert_approved_content(migrated_conn, title="not rendered")
    rendered_id = _insert_approved_content(migrated_conn, title="rendered")
    migrated_conn.execute(
        "UPDATE content SET rendered_video_path = '/tmp/x.mp4' WHERE id = ?", (rendered_id,)
    )
    migrated_conn.commit()

    items = select_approved_content(migrated_conn, limit=10)

    assert [c.title for c in items] == ["not rendered"]


def test_select_approved_content_excludes_non_approved_status(migrated_conn):
    _insert_approved_content(migrated_conn, status="DRAFT")
    items = select_approved_content(migrated_conn, limit=10)
    assert items == []


def test_render_batch_marks_success_and_persists_path(migrated_conn, tmp_path):
    _insert_approved_content(migrated_conn)
    config = Config()
    config.video.output_dir = str(tmp_path)

    outcomes = render_batch(migrated_conn, config, count=1)

    assert len(outcomes) == 1
    content, result, error = outcomes[0]
    assert error is None
    assert result is not None
    row = migrated_conn.execute(
        "SELECT status, rendered_video_path FROM content WHERE id = ?", (content.id,)
    ).fetchone()
    assert row["status"] == "READY"
    assert row["rendered_video_path"] == result.output_path


def test_render_batch_returns_empty_when_nothing_to_render(migrated_conn, tmp_path):
    config = Config()
    config.video.output_dir = str(tmp_path)
    assert render_batch(migrated_conn, config) == []


def test_render_batch_marks_failure_without_aborting_batch(migrated_conn, tmp_path):
    _insert_approved_content(migrated_conn, title="bad", script="")  # will fail: empty script
    _insert_approved_content(migrated_conn, title="good")
    config = Config()
    config.video.output_dir = str(tmp_path)

    outcomes = render_batch(migrated_conn, config, count=2)

    assert len(outcomes) == 2
    statuses = {
        content.title: (
            migrated_conn.execute(
                "SELECT status FROM content WHERE id = ?", (content.id,)
            ).fetchone()["status"]
        )
        for content, _, _ in outcomes
    }
    assert statuses["bad"] == "FAILED"
    assert statuses["good"] == "READY"
