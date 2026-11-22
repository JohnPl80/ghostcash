"""Video template configuration (CLAUDE.md section 17).

The three required templates (Information, List, Problem/Solution) each
have a fixed *scene structure* - that's the literal diagram from section
17 and lives in Python (see renderer.py), not YAML, since it's a small,
stable set of shapes rather than something that benefits from runtime
reconfiguration. What IS configurable without touching code, per one
YAML file per template in templates/video/, is everything about how a
template *looks*: colors, font, sizes, transition timing, and how many
"point" scenes a flexible template gets.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TEMPLATES_DIR = Path("templates/video")

# CLAUDE.md section 17's three templates.
TEMPLATE_NAMES: tuple[str, ...] = ("INFORMATION", "LIST", "PROBLEM_SOLUTION")

_FILENAME_BY_TEMPLATE: dict[str, str] = {
    "INFORMATION": "information.yaml",
    "LIST": "list.yaml",
    "PROBLEM_SOLUTION": "problem_solution.yaml",
}


class TemplateError(Exception):
    """Raised when a video template file is missing or invalid."""


@dataclass
class VideoTemplate:
    name: str
    description: str
    background_colors: list[str]
    cta_background_color: str
    text_color: str
    font_path: str
    font_family: str
    font_size: int
    subtitle_font_size: int
    fade_duration_seconds: float
    points_min: int
    points_max: int
    # Optional picture backgrounds (Aetherians): a folder of images shown
    # behind the scene text with a slow push-in, instead of flat colours.
    background_image_dir: str | None = None
    background_zoom: float = 0.08  # how far each picture zooms in over its scene
    background_dim: float = 0.45  # dark veil over the picture so text stays readable
    background_exclude: tuple[str, ...] = ()  # file-name parts never used (e.g. "cover_back")
    text_border: int = 0  # dark outline around the big scene text, in pixels
    accent_color: str | None = None  # thin line under the text on picture scenes
    # "all": every scene shows its text in big letters. "hook_and_cta": only
    # the opening line and the call to action do; the scenes between show the
    # picture with just the spoken subtitles.
    scene_text: str = "all"
    subtitle_margin: int = 120  # subtitle distance from the bottom edge, in pixels


def _require(raw: dict[str, Any], key: str, path: Path) -> Any:
    if key not in raw:
        raise TemplateError(f"{path}: missing required key {key!r}")
    return raw[key]


def load_template(content_type: str, templates_dir: Path = TEMPLATES_DIR) -> VideoTemplate:
    content_type = content_type.upper()
    if content_type not in TEMPLATE_NAMES:
        raise TemplateError(
            f"Unknown content_type {content_type!r}. Expected one of {TEMPLATE_NAMES}."
        )

    path = templates_dir / _FILENAME_BY_TEMPLATE[content_type]
    if not path.exists():
        raise TemplateError(f"Template file not found: {path}")

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise TemplateError(f"{path}: must contain a YAML mapping at the top level.")

    colors = _require(raw, "colors", path)
    font = _require(raw, "font", path)
    transition = raw.get("transition", {}) or {}
    points = raw.get("points", {}) or {}
    images = raw.get("background_images", {}) or {}

    try:
        template = VideoTemplate(
            name=content_type,
            description=raw.get("description", ""),
            background_colors=list(_require(colors, "background", path)),
            cta_background_color=str(_require(colors, "cta_background", path)),
            text_color=str(_require(colors, "text", path)),
            font_path=str(_require(font, "path", path)),
            font_family=str(font.get("family", "DejaVu Sans")),
            font_size=int(_require(font, "size", path)),
            subtitle_font_size=int(font.get("subtitle_size", 40)),
            fade_duration_seconds=float(transition.get("duration_seconds", 0.3)),
            points_min=int(points.get("min", 2)),
            points_max=int(points.get("max", 3)),
            background_image_dir=str(images["dir"]) if images.get("dir") else None,
            background_zoom=float(images.get("zoom", 0.08)),
            background_dim=float(images.get("dim", 0.45)),
            background_exclude=tuple(str(x) for x in images.get("exclude", []) or []),
            text_border=int(font.get("border", 0)),
            accent_color=str(colors["accent"]) if colors.get("accent") else None,
            scene_text=str(raw.get("scene_text", "all")),
            subtitle_margin=int(font.get("subtitle_margin", 120)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TemplateError(f"{path}: invalid template ({exc})") from exc
    if template.scene_text not in ("all", "hook_and_cta"):
        raise TemplateError(f"{path}: scene_text must be 'all' or 'hook_and_cta'")
    return template
