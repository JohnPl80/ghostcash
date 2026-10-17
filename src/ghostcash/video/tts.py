"""TTS abstraction (CLAUDE.md section 18).

The first implementation prioritizes free/local TTS, as required. It
goes one step further than the obvious choice (installing espeak-ng as
a separate system package): ffmpeg ships an optional built-in `flite`
source filter (libflite), so when ffmpeg is built with it - the
standard Ubuntu/Debian `ffmpeg` package is - speech synthesis needs
*zero* additional dependencies beyond the ffmpeg binary GhostCash
already requires. That's about as "minimize external dependencies" as
section 4 asks for.

Text is always written to a temp file and referenced via flite's
`textfile=` parameter rather than embedded inline in the filter graph
string. ffmpeg's filter syntax treats `:`, `'`, `,`, `[`, `]` as
structural characters, and video scripts routinely contain colons,
apostrophes, and commas - `textfile=` sidesteps escaping all of that
correctly by never putting the text in the filter string at all.
"""

from __future__ import annotations

import subprocess
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


class TTSError(Exception):
    """Raised when speech synthesis fails."""


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, text: str, output_path: str) -> None:
        """Write synthesized speech for `text` to `output_path` (WAV)."""


def is_flite_available(ffmpeg_binary: str = "ffmpeg") -> bool:
    """Check whether the ffmpeg binary in use was built with libflite support."""
    try:
        result = subprocess.run(
            [ffmpeg_binary, "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and " flite " in result.stdout


class FliteTTSProvider(TTSProvider):
    """Local, free TTS via ffmpeg's built-in `flite` lavfi source filter."""

    DEFAULT_VOICE = "slt"

    def __init__(
        self,
        voice: str = DEFAULT_VOICE,
        ffmpeg_binary: str = "ffmpeg",
        timeout_seconds: int = 120,
    ) -> None:
        self._voice = voice
        self._ffmpeg_binary = ffmpeg_binary
        self._timeout_seconds = timeout_seconds

    def synthesize(self, text: str, output_path: str) -> None:
        if not text.strip():
            raise TTSError("Cannot synthesize empty text.")

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="ghostcash-tts-") as tmp_dir:
            text_path = Path(tmp_dir) / "script.txt"
            text_path.write_text(text, encoding="utf-8")

            cmd = [
                self._ffmpeg_binary,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                f"flite=textfile={text_path}:voice={self._voice}",
                str(output_path),
            ]
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=self._timeout_seconds
                )
            except FileNotFoundError as exc:
                raise TTSError(f"ffmpeg binary not found: {self._ffmpeg_binary!r}") from exc
            except subprocess.TimeoutExpired as exc:
                raise TTSError("ffmpeg/flite synthesis timed out.") from exc

            if result.returncode != 0:
                hint = ""
                if "No such filter" in result.stderr or "flite" in result.stderr.lower():
                    hint = (
                        " Your ffmpeg build may not include libflite support "
                        "(--enable-libflite). The standard Ubuntu/Debian ffmpeg "
                        "package includes it; a minimal custom build may not."
                    )
                raise TTSError(f"ffmpeg/flite synthesis failed: {result.stderr.strip()}{hint}")
