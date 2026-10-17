from __future__ import annotations

import pytest

from ghostcash.video.templates import TEMPLATE_NAMES, TemplateError, load_template


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_all_shipped_templates_load(name):
    template = load_template(name)
    assert template.name == name
    assert len(template.background_colors) >= 1
    assert template.cta_background_color
    assert template.font_path
    assert template.font_family
    assert template.font_size > 0


def test_load_template_is_case_insensitive():
    assert load_template("information").name == "INFORMATION"


def test_load_template_rejects_unknown_type():
    with pytest.raises(TemplateError, match="Unknown content_type"):
        load_template("NOT_A_REAL_TEMPLATE")


def test_load_template_rejects_missing_file(tmp_path):
    with pytest.raises(TemplateError, match="not found"):
        load_template("INFORMATION", templates_dir=tmp_path)


def test_load_template_rejects_non_mapping_yaml(tmp_path):
    (tmp_path / "information.yaml").write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="mapping"):
        load_template("INFORMATION", templates_dir=tmp_path)


def test_load_template_rejects_missing_required_key(tmp_path):
    (tmp_path / "information.yaml").write_text("description: no colors here\n", encoding="utf-8")
    with pytest.raises(TemplateError, match="colors"):
        load_template("INFORMATION", templates_dir=tmp_path)


def test_load_template_applies_defaults_for_optional_keys(tmp_path):
    (tmp_path / "information.yaml").write_text(
        "colors:\n  background: ['#000000']\n  cta_background: '#FFFFFF'\n  text: '#FFFFFF'\n"
        "font:\n  path: /some/font.ttf\n  size: 50\n",
        encoding="utf-8",
    )
    template = load_template("INFORMATION", templates_dir=tmp_path)
    assert template.font_family == "DejaVu Sans"
    assert template.subtitle_font_size == 40
    assert template.fade_duration_seconds == pytest.approx(0.3)
    assert template.points_min == 2
    assert template.points_max == 3
