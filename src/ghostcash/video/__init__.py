"""FFmpeg-based video rendering engine and templates (CLAUDE.md sections 16-18).

`render_content()` turns one piece of APPROVED content into a finished
MP4. `render_batch()` does that for a batch of approved-but-unrendered
content and updates their status accordingly - see cli/commands/render.py
for the CLI entry point.
"""

from ghostcash.video.renderer import (
    RenderError,
    RenderResult,
    Scene,
    get_tts_provider,
    render_batch,
    render_content,
    select_approved_content,
)
from ghostcash.video.templates import TEMPLATE_NAMES, TemplateError, VideoTemplate, load_template
from ghostcash.video.tts import FliteTTSProvider, TTSError, TTSProvider, is_flite_available

__all__ = [
    "TEMPLATE_NAMES",
    "FliteTTSProvider",
    "RenderError",
    "RenderResult",
    "Scene",
    "TTSError",
    "TTSProvider",
    "TemplateError",
    "VideoTemplate",
    "get_tts_provider",
    "is_flite_available",
    "load_template",
    "render_batch",
    "render_content",
    "select_approved_content",
]
