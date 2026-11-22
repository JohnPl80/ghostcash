"""Picture backgrounds with a slow zoom (Aetherians templates)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from ghostcash.config.schema import Config
from ghostcash.models.content import Content
from ghostcash.video.renderer import pick_backgrounds, render_content
from ghostcash.video.templates import TEMPLATE_NAMES, TemplateError, load_template

AETH = Path("templates/video/aetherians")


def _content(topic: str = "Leo", content_id: int = 3) -> Content:
    return Content(
        id=content_id,
        topic=topic,
        category="ZODIAC",
        title=topic,
        hook=f"Why {topic} belongs to the Sun.",
        script="Leo was given to the Sun. Its bright star sits on the Sun's path.",
        caption="c",
        cta="Follow for more.",
        content_type="INFORMATION",
        status="APPROVED",
    )


def _image(path: Path, color: str = "red") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (400, 600), color).save(path)
    return path


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_aetherians_templates_load_with_pictures(name):
    template = load_template(name, AETH)
    assert template.background_image_dir == "assets/brands/aetherians/backgrounds"
    assert template.scene_text == "hook_and_cta"
    assert "cover_back" in template.background_exclude
    assert Path(template.font_path).is_file()


def test_bad_scene_text_is_rejected(tmp_path):
    raw = (
        (AETH / "information.yaml")
        .read_text()
        .replace("scene_text: hook_and_cta", "scene_text: sometimes")
    )
    (tmp_path / "information.yaml").write_text(raw)
    with pytest.raises(TemplateError):
        load_template("INFORMATION", tmp_path)


def _template_with(folder: Path):
    template = load_template("INFORMATION", AETH)
    template.background_image_dir = str(folder)
    return template


def test_picture_named_after_the_topic_opens_and_cover_closes(tmp_path):
    zodiac = tmp_path / "zodiac"
    for name in ["aries", "leo", "virgo", "cover_front", "cover_back"]:
        _image(zodiac / f"{name}.jpg")
    _image(tmp_path / "crystals" / "amethyst.jpg")
    picks = pick_backgrounds(_content("Leo"), _template_with(tmp_path), 5)
    assert Path(picks[0]).stem == "leo"
    assert Path(picks[-1]).stem == "cover_front"
    assert not any("cover_back" in p for p in picks)
    # Same book before other books.
    assert all(Path(p).parent == zodiac for p in picks[:3])
    assert Path(picks[3]).stem == "amethyst"
    # A re-render picks the same pictures.
    assert picks == pick_backgrounds(_content("Leo"), _template_with(tmp_path), 5)


def test_multi_word_names_match(tmp_path):
    for name in ["lapis_lazuli", "black_tourmaline", "garnet"]:
        _image(tmp_path / f"{name}.jpg")
    assert Path(
        pick_backgrounds(_content("Lapis Lazuli"), _template_with(tmp_path), 1)[0]
    ).stem == ("lapis_lazuli")
    picks = pick_backgrounds(_content("Tourmaline"), _template_with(tmp_path), 1)
    assert Path(picks[0]).stem == "black_tourmaline"


def test_no_folder_means_plain_colours(tmp_path):
    template = load_template("INFORMATION", AETH)
    template.background_image_dir = str(tmp_path / "missing")
    assert pick_backgrounds(_content(), template, 4) == []
    template.background_image_dir = None
    assert pick_backgrounds(_content(), template, 4) == []


def _flite_available() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return " flite " in out.stdout


@pytest.mark.skipif(not _flite_available(), reason="ffmpeg without libflite")
def test_render_shows_the_picture_in_colour(tmp_path):
    folder = tmp_path / "bg"
    _image(folder / "leo.jpg", "#C02020")
    templates = tmp_path / "templates"
    templates.mkdir()
    for name in ["information", "list", "problem_solution"]:
        raw = (
            (AETH / f"{name}.yaml")
            .read_text()
            .replace("assets/brands/aetherians/backgrounds", str(folder))
        )
        raw = raw.replace("assets/fonts/", str(Path("assets/fonts").resolve()) + "/")
        (templates / f"{name}.yaml").write_text(raw)
    config = Config()
    config.video.tts_provider = "flite"
    config.video.output_dir = str(tmp_path / "out")
    config.video.audio_cache_dir = str(tmp_path / "audio")
    result = render_content(_content(), config, templates_dir=templates)
    assert all(scene.image for scene in result.scenes)
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", "1", "-i", result.output_path, "-frames:v", "1",
         str(frame)],
        check=True,
    )  # fmt: skip
    r, g, b = Image.open(frame).convert("RGB").resize((1, 1)).getpixel((0, 0))
    assert r > g + 30 and r > b + 30  # still red: the veil darkens, it does not grey out
