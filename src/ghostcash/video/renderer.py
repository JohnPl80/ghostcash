"""Video rendering (CLAUDE.md sections 16-17).

render_content() turns one piece of APPROVED content into a finished
1080x1920 H.264 MP4: TTS narration (section 18), template-driven text
overlays with simple fade motion, and synced subtitles - no real
video/image assets and no AI video generation, per section 16.

The ffmpeg filter graph (color sources -> drawtext -> fade -> concat ->
subtitles) was built and verified interactively against the real
ffmpeg/libass in this environment, including one non-obvious bug fix:
see subtitles.py's docstring for why subtitles are written as .ass
rather than .srt.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ghostcash.config.schema import Config
from ghostcash.models.content import Content
from ghostcash.video.subtitles import write_ass_subtitles
from ghostcash.video.templates import TEMPLATES_DIR, VideoTemplate, load_template
from ghostcash.video.text_utils import chunk_evenly, split_sentences, wrap_text_to_width
from ghostcash.video.tts import FliteTTSProvider, TTSError, TTSProvider


class RenderError(Exception):
    """Raised when rendering a piece of content into a video fails."""


@dataclass
class Scene:
    slot: str
    text: str
    background_color: str
    duration_seconds: float = 0.0


@dataclass
class RenderResult:
    output_path: str
    duration_seconds: float
    scene_count: int
    scenes: list[Scene] = field(default_factory=list)


def get_tts_provider(config: Config) -> TTSProvider:
    provider_name = (config.video.tts_provider or "").strip().lower()
    if provider_name == "flite":
        return FliteTTSProvider(
            voice=config.video.tts_voice, ffmpeg_binary=config.video.ffmpeg_binary
        )
    raise RenderError(f"Unknown TTS provider: {provider_name!r}. Currently implemented: flite.")


def _probe_duration_seconds(path: str | Path, ffprobe_binary: str) -> float:
    try:
        result = subprocess.run(
            [
                ffprobe_binary,
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RenderError(f"ffprobe failed on {path}: {exc}") from exc
    if result.returncode != 0 or not result.stdout.strip():
        raise RenderError(f"Could not determine duration of {path}: {result.stderr.strip()}")
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise RenderError(f"Unexpected ffprobe output for {path}: {result.stdout!r}") from exc


def _count_available_segments(content: Content) -> int:
    visual_items = _parse_visual_sequence(content)
    if visual_items:
        return len(visual_items)
    return max(len(split_sentences(content.script or "")), 1)


def _parse_visual_sequence(content: Content) -> list[str]:
    if not content.visual_sequence_json:
        return []
    try:
        parsed = json.loads(content.visual_sequence_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _derive_middle_segments(content: Content, count: int) -> list[str]:
    """Return exactly `count` non-empty text segments for a template's
    middle scenes: visual_sequence entries when there are enough
    (they already describe on-screen beats), otherwise the script split
    into `count` roughly-equal sentence groups."""
    visual_items = _parse_visual_sequence(content)
    if len(visual_items) >= count:
        return visual_items[:count]

    sentences = split_sentences(content.script or "") or [content.script or ""]
    groups = chunk_evenly(sentences, count)
    segments = [" ".join(group).strip() for group in groups]

    fallback_text = content.title or content.topic or ""
    for index, segment in enumerate(segments):
        if segment:
            fallback_text = segment
        else:
            segments[index] = fallback_text
    return segments


def _build_scenes(content: Content, template: VideoTemplate) -> list[Scene]:
    hook = (content.hook or "").strip() or (content.title or content.topic)
    cta = (content.cta or "").strip() or "Follow for more."
    colors = template.background_colors

    if template.name == "PROBLEM_SOLUTION":
        middle = _derive_middle_segments(content, 2)
        scenes = [
            Scene("PROBLEM", hook, colors[0 % len(colors)]),
            Scene("WHY_IT_HAPPENS", middle[0], colors[1 % len(colors)]),
            Scene("SOLUTION", middle[1], colors[2 % len(colors)]),
        ]
    else:
        available = _count_available_segments(content)
        point_count = max(template.points_min, min(template.points_max, available))
        middle = _derive_middle_segments(content, point_count)
        scenes = [Scene("HOOK", hook, colors[0 % len(colors)])]
        for index, text in enumerate(middle):
            label = f"{index + 1}. {text}" if template.name == "LIST" else text
            scenes.append(Scene(f"POINT_{index + 1}", label, colors[(index + 1) % len(colors)]))

    scenes.append(Scene("CTA", cta, template.cta_background_color))
    return scenes


def _allocate_durations(
    scenes: list[Scene], total_duration: float, min_scene_seconds: float
) -> None:
    weights = [max(len(scene.text), 1) for scene in scenes]
    total_weight = sum(weights)
    remaining = total_duration

    for index, scene in enumerate(scenes):
        if index == len(scenes) - 1:
            scene.duration_seconds = max(min_scene_seconds, remaining)
        else:
            share = (weights[index] / total_weight) * total_duration
            scene.duration_seconds = max(min_scene_seconds, share)
            remaining -= scene.duration_seconds


def _escape_filter_path(path: str) -> str:
    """Defensive escaping for paths used as ffmpeg filtergraph argument
    values (drawtext's fontfile/textfile, subtitles' filename). Linux
    temp-file paths won't contain these characters in practice, but
    escape rather than assume."""
    return path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _build_filter_complex(
    scenes: list[Scene],
    template: VideoTemplate,
    ass_path: Path,
    width: int,
    height: int,
    work_dir: Path,
) -> str:
    parts: list[str] = []
    scene_labels: list[str] = []
    fade_duration = template.fade_duration_seconds
    font_path = _escape_filter_path(template.font_path)

    for index, scene in enumerate(scenes):
        text_path = work_dir / f"scene_{index}.txt"
        wrapped_text = wrap_text_to_width(
            scene.text,
            font_path=template.font_path,
            font_size=template.font_size,
            max_width_px=int(width * 0.85),
        )
        text_path.write_text(wrapped_text, encoding="utf-8")

        bg_label, txt_label, out_label = f"bg{index}", f"txt{index}", f"scene{index}"
        parts.append(
            f"color=c={scene.background_color}:s={width}x{height}:"
            f"d={scene.duration_seconds:.3f}[{bg_label}]"
        )
        parts.append(
            f"[{bg_label}]drawtext=fontfile={font_path}:"
            f"textfile={_escape_filter_path(str(text_path))}:"
            f"fontsize={template.font_size}:fontcolor={template.text_color}:"
            f"line_spacing=14:x=(w-text_w)/2:y=(h-text_h)/2:box=0[{txt_label}]"
        )
        fade_out_start = max(0.0, scene.duration_seconds - fade_duration)
        parts.append(
            f"[{txt_label}]fade=t=in:st=0:d={fade_duration:.3f},"
            f"fade=t=out:st={fade_out_start:.3f}:d={fade_duration:.3f}[{out_label}]"
        )
        scene_labels.append(f"[{out_label}]")

    parts.append(f"{''.join(scene_labels)}concat=n={len(scenes)}:v=1:a=0[video_concat]")
    parts.append(f"[video_concat]subtitles={_escape_filter_path(str(ass_path))}[final_video]")
    return ";".join(parts)


def render_content(
    content: Content,
    config: Config,
    tts_provider: TTSProvider | None = None,
    templates_dir: Path = TEMPLATES_DIR,
) -> RenderResult:
    if not (content.script or "").strip():
        raise RenderError("Content has no script to render.")
    if not content.content_type:
        raise RenderError("Content has no content_type; cannot choose a template.")

    template = load_template(content.content_type, templates_dir)
    tts_provider = tts_provider or get_tts_provider(config)
    video_cfg = config.video

    output_dir = Path(video_cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"content_{content.id}" if content.id is not None else "content_preview"
    output_path = output_dir / f"{stem}.{video_cfg.format}"

    hook = (content.hook or "").strip() or (content.title or content.topic)
    cta = (content.cta or "").strip() or "Follow for more."
    full_narration = " ".join(part for part in [hook, (content.script or "").strip(), cta] if part)
    if not full_narration.strip():
        raise RenderError("Nothing to narrate for this content.")

    with tempfile.TemporaryDirectory(prefix="ghostcash-render-") as work_dir:
        work_path = Path(work_dir)
        audio_path = work_path / "narration.wav"
        try:
            tts_provider.synthesize(full_narration, str(audio_path))
        except TTSError as exc:
            raise RenderError(f"TTS synthesis failed: {exc}") from exc

        total_duration = _probe_duration_seconds(audio_path, video_cfg.ffprobe_binary)

        scenes = _build_scenes(content, template)
        min_scene_seconds = template.fade_duration_seconds * 2 + 0.3
        _allocate_durations(scenes, total_duration, min_scene_seconds)

        ass_path = work_path / "subtitles.ass"
        write_ass_subtitles(
            full_narration,
            total_duration,
            ass_path,
            width=video_cfg.width,
            height=video_cfg.height,
            font_family=template.font_family,
            font_size=template.subtitle_font_size,
            text_color=template.text_color,
        )

        filter_complex = _build_filter_complex(
            scenes, template, ass_path, video_cfg.width, video_cfg.height, work_path
        )

        cmd = [
            video_cfg.ffmpeg_binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(audio_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "[final_video]",
            "-map",
            "0:a",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        except FileNotFoundError as exc:
            raise RenderError(f"ffmpeg binary not found: {video_cfg.ffmpeg_binary!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RenderError("ffmpeg render timed out.") from exc

        if result.returncode != 0:
            raise RenderError(f"ffmpeg render failed: {result.stderr.strip()[-2000:]}")

    final_duration = _probe_duration_seconds(output_path, video_cfg.ffprobe_binary)
    return RenderResult(
        output_path=str(output_path),
        duration_seconds=final_duration,
        scene_count=len(scenes),
        scenes=scenes,
    )


def select_approved_content(conn: sqlite3.Connection, limit: int) -> list[Content]:
    """Approved content without a rendered video yet, oldest first.

    Idempotent by construction (section 57): once rendered_video_path is
    set, a row never comes back from this query, so re-running `render`
    can't double-render the same content.
    """
    rows = conn.execute(
        "SELECT * FROM content WHERE status = 'APPROVED' AND rendered_video_path IS NULL "
        "ORDER BY id ASC LIMIT ?",
        (limit,),
    ).fetchall()
    return [Content.from_row(row) for row in rows]


def reset_missing_renders(conn: sqlite3.Connection) -> list[int]:
    """Send READY content whose video file no longer exists back to be re-rendered.

    GitHub Actions runners start with an empty disk every time, while the
    database is restored from a cache (see .github/workflows). So a video
    rendered in one run is gone by the next, yet its row still says READY
    with a rendered_video_path pointing at nothing. `publish` would pick
    those rows first (oldest first), fail to find the file, and leave them
    READY - blocking every newer video behind them.

    The video is fully determined by the stored script and template, so
    the cure is cheap and local (no AI call): put such rows back to
    APPROVED with no rendered path, and `render` produces the file again.
    Only READY rows are touched - SCHEDULED/published content no longer
    needs its local file. Returns the ids that were reset (oldest first);
    idempotent, and a no-op when every file is present.
    """
    rows = conn.execute(
        "SELECT id, rendered_video_path FROM content "
        "WHERE status = 'READY' AND rendered_video_path IS NOT NULL ORDER BY id ASC"
    ).fetchall()
    missing_ids = [row[0] for row in rows if not Path(row[1]).exists()]
    for content_id in missing_ids:
        conn.execute(
            "UPDATE content SET status = 'APPROVED', rendered_video_path = NULL, "
            "rendered_duration_seconds = NULL WHERE id = ? AND status = 'READY'",
            (content_id,),
        )
    if missing_ids:
        conn.commit()
    return missing_ids


def _mark_rendered(conn: sqlite3.Connection, content_id: int, result: RenderResult) -> None:
    conn.execute(
        "UPDATE content SET status = 'READY', rendered_video_path = ?, "
        "rendered_duration_seconds = ? WHERE id = ?",
        (result.output_path, result.duration_seconds, content_id),
    )
    conn.commit()


def _mark_failed(conn: sqlite3.Connection, content_id: int) -> None:
    conn.execute("UPDATE content SET status = 'FAILED' WHERE id = ?", (content_id,))
    conn.commit()


def render_batch(
    conn: sqlite3.Connection,
    config: Config,
    count: int | None = None,
    tts_provider: TTSProvider | None = None,
    templates_dir: Path = TEMPLATES_DIR,
) -> list[tuple[Content, RenderResult | None, str | None]]:
    """Render up to `count` approved-but-unrendered content items.

    Returns a list of (content, result_or_None, error_or_None) tuples -
    one entry per content item attempted, so callers can report both
    successes and per-item failures without a single bad render
    aborting the whole batch. Returns [] if there was nothing to do
    (not an error).
    """
    requested_count = count if count is not None else config.video.render_per_run
    if requested_count < 1:
        return []

    reset_missing_renders(conn)
    items = select_approved_content(conn, requested_count)
    if not items:
        return []

    tts_provider = tts_provider or get_tts_provider(config)
    results: list[tuple[Content, RenderResult | None, str | None]] = []

    for content in items:
        assert content.id is not None
        try:
            result = render_content(
                content, config, tts_provider=tts_provider, templates_dir=templates_dir
            )
        except RenderError as exc:
            _mark_failed(conn, content.id)
            results.append((content, None, str(exc)))
            continue

        _mark_rendered(conn, content.id, result)
        content.status = "READY"
        content.rendered_video_path = result.output_path
        content.rendered_duration_seconds = result.duration_seconds
        results.append((content, result, None))

    return results
