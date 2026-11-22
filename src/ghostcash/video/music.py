"""Background music under the narration.

A channel lists its tracks in `video.music_files`; each video gets one of
them (chosen by content id, so a re-render picks the same track). The edit:

- The music is lined up so that its own ending coincides with the end of the
  video ("back-timed"). A track longer than the video starts partway in; a
  track shorter than the video starts partway into the video (silence before).
- It fades in where it starts and fades out over the last moment, so neither
  edge is abrupt.
- The narration is brought to `narration_target_lufs`; each track is
  measured once and brought to `music_target_lufs`, so quiet
  and loud tracks sit at the same level, well under the voice; while the
  narrator speaks it is pulled down a little further (sidechain "ducking").
- The video runs `music_tail_seconds` past the last spoken word, so the
  music can finish.

Any problem with the music (missing file, ffmpeg error) is logged and the
video is rendered without it; music never stops a render.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ghostcash.config.schema import VideoConfig

logger = logging.getLogger(__name__)

SAMPLE_RATE = 48000
_loudness_cache: dict[str, float] = {}


@dataclass(frozen=True)
class MusicPlan:
    """Where the track sits in the video (all in seconds)."""

    offset_in_track: float  # where in the track playback starts
    start_in_video: float  # when the music starts in the video
    length: float  # how much of the track is heard


def plan_music(track_seconds: float, video_seconds: float) -> MusicPlan:
    """Back-time the track so it ends exactly when the video ends."""
    if track_seconds >= video_seconds:
        return MusicPlan(track_seconds - video_seconds, 0.0, video_seconds)
    return MusicPlan(0.0, video_seconds - track_seconds, track_seconds)


def pick_track(video_cfg: VideoConfig, content_id: int | None) -> Path | None:
    files = [Path(f) for f in video_cfg.music_files if str(f).strip()]
    if not files:
        return None
    track = files[(content_id or 0) % len(files)]
    if not track.is_file():
        logger.warning("Music file %s not found; rendering without music.", track)
        return None
    return track


def measure_lufs(track: Path, ffmpeg_binary: str) -> float | None:
    key = str(track.resolve())
    if key in _loudness_cache:
        return _loudness_cache[key]
    try:
        result = subprocess.run(
            [
                ffmpeg_binary,
                "-hide_banner",
                "-nostats",
                "-i",
                str(track),
                "-af",
                "ebur128=framelog=quiet",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not measure loudness of %s: %s", track, exc)
        return None
    found = re.findall(r"I:\s+(-?[\d.]+) LUFS", result.stderr)
    if not found:
        return None
    _loudness_cache[key] = float(found[-1])
    return _loudness_cache[key]


def build_mix_filter(
    plan: MusicPlan,
    video_seconds: float,
    gain_db: float,
    fade_in: float,
    fade_out: float,
    duck: bool,
    voice_gain_db: float = 0.0,
) -> str:
    fade_in = max(0.0, min(fade_in, plan.length / 2))
    fade_out = max(0.0, min(fade_out, plan.length / 2))
    delay_ms = int(round(plan.start_in_video * 1000))
    fmt = f"aformat=sample_rates={SAMPLE_RATE}:channel_layouts=stereo"
    music = (
        f"[1:a]atrim=start={plan.offset_in_track:.3f}:duration={plan.length:.3f},"
        f"asetpts=PTS-STARTPTS,{fmt},volume={gain_db:.2f}dB,"
        f"afade=t=in:st=0:d={fade_in:.3f},"
        f"afade=t=out:st={plan.length - fade_out:.3f}:d={fade_out:.3f},"
        f"adelay={delay_ms}|{delay_ms},apad=whole_dur={video_seconds:.3f}[m]"
    )
    voice = f"[0:a]{fmt},volume={voice_gain_db:.2f}dB,apad=whole_dur={video_seconds:.3f}"
    if duck:
        return (
            f"{music};{voice},asplit=2[v][key];"
            "[m][key]sidechaincompress=threshold=0.05:ratio=3:attack=30:release=500[md];"
            f"[v][md]amix=inputs=2:duration=first:normalize=0,"
            f"atrim=end={video_seconds:.3f},alimiter=limit=0.95[out]"
        )
    return (
        f"{music};{voice}[v];"
        f"[v][m]amix=inputs=2:duration=first:normalize=0,atrim=end={video_seconds:.3f},"
        "alimiter=limit=0.95[out]"
    )


def mix_music(
    narration: Path,
    track: Path,
    narration_seconds: float,
    work_dir: Path,
    video_cfg: VideoConfig,
    probe_duration,
) -> tuple[Path, float] | None:
    """Mix `track` under `narration`. Returns (mixed audio, video length) or
    None when the music cannot be used (the caller then renders without it)."""
    try:
        track_seconds = probe_duration(track, video_cfg.ffprobe_binary)
    except Exception as exc:  # noqa: BLE001 - any failure: render without music
        logger.warning("Could not read music file %s: %s", track, exc)
        return None
    video_seconds = narration_seconds + max(0.0, video_cfg.music_tail_seconds)
    plan = plan_music(track_seconds, video_seconds)
    measured = measure_lufs(track, video_cfg.ffmpeg_binary)
    gain_db = (video_cfg.music_target_lufs - measured) if measured is not None else -16.0
    # The voice is levelled too, so "music X dB under the voice" holds whatever
    # voice is used (Gemini voices come out quiet, around -22 LUFS).
    voice_measured = measure_lufs(narration, video_cfg.ffmpeg_binary)
    voice_gain_db = (
        video_cfg.narration_target_lufs - voice_measured if voice_measured is not None else 0.0
    )
    graph = build_mix_filter(
        plan,
        video_seconds,
        gain_db,
        video_cfg.music_fade_in_seconds,
        video_cfg.music_fade_out_seconds,
        video_cfg.music_duck,
        voice_gain_db=max(-12.0, min(12.0, voice_gain_db)),
    )
    out = work_dir / "narration_with_music.wav"
    cmd = [
        video_cfg.ffmpeg_binary,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(narration),
        "-i",
        str(track),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-ar",
        str(SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(out),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Music mix failed (%s); rendering without music.", exc)
        return None
    if result.returncode != 0:
        logger.warning("Music mix failed; rendering without music: %s", result.stderr[-500:])
        return None
    return out, video_seconds
