"""`ghostcash voice-test` - hear a voice before any video uses it.

Synthesizes one short sample with the configured main voice and writes it
as a WAV file you can play. Unlike `render`, there is NO fallback here: if
the Gemini voice does not work, the real error is printed, which is the
point - a render would quietly use the backup voice and hide it.

Options let you audition without editing config.yaml:
  --voice NAME     a Gemini voice (Kore, Puck, Charon, Zephyr, Fenrir, ...)
  --model NAME     a Gemini speech model
  --style TEXT     how it should sound ("calm and warm", "energetic", ...)
  --provider NAME  gemini or flite, to compare the two
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from ghostcash.config.loader import ConfigError, load_config
from ghostcash.video.renderer import RenderError, build_gemini_tts
from ghostcash.video.tts import FliteTTSProvider, GeminiTTSProvider, TTSError, TTSProvider

DEFAULT_SAMPLE_TEXT = (
    "Did you know you can undo almost anything with one shortcut? "
    "Press control Z. Press it again to go back further. "
    "Follow for more tips that save you time."
)


def _duration_seconds(path: Path, ffprobe_binary: str) -> float | None:
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
            timeout=15,
        )
        return float(result.stdout.strip())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def run(args: argparse.Namespace) -> int:
    try:
        config = load_config(args.config_path)
    except ConfigError as exc:
        print(f"[error] {exc}")
        return 1

    provider_name = (args.provider or config.video.tts_provider or "").strip().lower()
    output_path = Path(args.output)

    provider: TTSProvider
    if provider_name == "gemini":
        try:
            provider = build_gemini_tts(
                config, voice=args.voice, model=args.model, style=args.style
            )
        except RenderError as exc:
            print(f"[error] {exc}")
            return 1
        assert isinstance(provider, GeminiTTSProvider)
        print(f"Voice test: Gemini model '{provider.model}', voice '{provider.voice}'")
    elif provider_name == "flite":
        provider = FliteTTSProvider(
            voice=args.voice or config.video.tts_voice, ffmpeg_binary=config.video.ffmpeg_binary
        )
        print("Voice test: flite (the robotic built-in voice)")
    else:
        print(f"[error] unknown provider {provider_name!r}; use 'gemini' or 'flite'.")
        return 1

    try:
        provider.synthesize(args.text, str(output_path))
    except TTSError as exc:
        print(f"[error] the voice did not work: {exc}")
        return 1

    seconds = _duration_seconds(output_path, config.video.ffprobe_binary)
    length = f", {seconds:.1f} seconds" if seconds is not None else ""
    print(f"[ok] wrote {output_path.resolve()}{length}")
    if isinstance(provider, GeminiTTSProvider) and provider.working_shape:
        print(f"     request format the API accepted: {provider.working_shape}")
    return 0
