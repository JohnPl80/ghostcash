"""Background music: back-timed placement, levelling, ducking (video/music.py)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from ghostcash.config.schema import Config, VideoConfig
from ghostcash.models.content import Content
from ghostcash.video.music import build_mix_filter, pick_track, plan_music
from ghostcash.video.renderer import _probe_duration_seconds, render_content

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def test_longer_track_starts_partway_in_and_ends_with_the_video():
    plan = plan_music(track_seconds=30.0, video_seconds=22.0)
    assert plan.offset_in_track == pytest.approx(8.0)
    assert plan.start_in_video == 0.0
    assert plan.length == pytest.approx(22.0)


def test_shorter_track_starts_later_in_the_video_and_ends_with_it():
    # The owner's rule (2026-11-22): music must reach the end of the clip,
    # even if there is no music at the beginning.
    plan = plan_music(track_seconds=30.0, video_seconds=42.0)
    assert plan.offset_in_track == 0.0
    assert plan.start_in_video == pytest.approx(12.0)
    assert plan.start_in_video + plan.length == pytest.approx(42.0)


def test_equal_lengths_fill_exactly():
    plan = plan_music(30.0, 30.0)
    assert (plan.offset_in_track, plan.start_in_video, plan.length) == (0.0, 0.0, 30.0)


def test_mix_filter_fades_delays_and_ducks():
    graph = build_mix_filter(plan_music(30.0, 42.0), 42.0, -9.5, 1.5, 1.2, duck=True)
    assert "adelay=12000|12000" in graph
    assert "afade=t=in:st=0:d=1.500" in graph
    assert "afade=t=out:st=28.800:d=1.200" in graph
    assert "volume=-9.50dB" in graph
    assert "sidechaincompress" in graph
    assert "normalize=0" in graph  # amix must not halve the voice
    assert "sidechaincompress" not in build_mix_filter(plan_music(30, 20), 20, 0, 1, 1, duck=False)


def test_pick_track_is_stable_and_skips_missing_files(tmp_path):
    a, b = tmp_path / "a.wav", tmp_path / "b.wav"
    a.write_bytes(b"x")
    b.write_bytes(b"x")
    cfg = VideoConfig()
    assert pick_track(cfg, 1) is None  # no music configured
    cfg.music_files = [str(a), str(b)]
    assert pick_track(cfg, 4) == a and pick_track(cfg, 5) == b
    assert pick_track(cfg, 5) == pick_track(cfg, 5)
    cfg.music_files = [str(tmp_path / "missing.wav")]
    assert pick_track(cfg, 1) is None


def _content() -> Content:
    return Content(
        id=3,
        topic="Leo",
        category="TIME",
        title="t",
        hook="Why the lion belongs to the Sun.",
        script="Leo was given to the Sun. Its bright star sits on the Sun's path.",
        caption="c",
        cta="Follow for more.",
        content_type="INFORMATION",
        status="APPROVED",
    )


def _config(tmp_path: Path) -> Config:
    config = Config()
    config.video.tts_provider = "flite"
    config.video.output_dir = str(tmp_path / "out")
    config.video.audio_cache_dir = str(tmp_path / "audio")
    return config


def _flite_available() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return " flite " in out.stdout


@pytest.mark.skipif(not _flite_available(), reason="ffmpeg without libflite")
def test_render_with_music_adds_the_tail_and_music_reaches_the_end(tmp_path):
    track = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=4",
            "-ac",
            "2",
            str(track),
        ],
        check=True,
    )
    plain_cfg = _config(tmp_path)
    plain = render_content(_content(), plain_cfg)
    plain_len = plain.duration_seconds

    cfg = _config(tmp_path)
    cfg.video.output_dir = str(tmp_path / "out2")
    cfg.video.music_files = [str(track)]
    with_music = render_content(_content(), cfg)

    assert with_music.duration_seconds == pytest.approx(plain_len + 1.5, abs=0.25)
    # The last second (after the voice) is music, not silence.
    tail = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-sseof",
            "-1.2",
            "-t",
            "0.6",
            "-i",
            with_music.output_path,
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    ).stderr
    mean = float(tail.split("mean_volume:")[1].split("dB")[0])
    assert mean > -60


@pytest.mark.skipif(not _flite_available(), reason="ffmpeg without libflite")
def test_missing_music_file_renders_without_music(tmp_path):
    cfg = _config(tmp_path)
    cfg.video.music_files = [str(tmp_path / "gone.wav")]
    result = render_content(_content(), cfg)
    audio_len = _probe_duration_seconds(result.output_path, "ffprobe")
    assert result.duration_seconds == pytest.approx(audio_len, abs=0.25)
