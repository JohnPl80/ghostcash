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

Two providers exist:

  * FliteTTSProvider - free, offline, zero setup, but a plainly robotic
    voice. The reliable floor.
  * GeminiTTSProvider - Google's Gemini speech models, which sound like a
    person. Uses the same AI_API_KEY as the text generation and has a free
    tier. Needs the network, so it can fail (quota, outage), and for that
    FallbackTTSProvider wraps it: when the good voice fails, the video is
    still made with flite, and the fact is recorded so the CLI can say so
    out loud instead of quietly shipping a robotic voice.
"""

from __future__ import annotations

import base64
import logging
import re
import subprocess
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Gemini speech (neural voice, same API key as the text model)
# ---------------------------------------------------------------------------

# Transient failures worth retrying (same reasoning as ai/gemini.py).
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_DEFAULT_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 45.0)

# Gemini speech models answer raw 24 kHz mono 16-bit PCM when no container
# is sent; this is the rate used when a response does not state its own.
_DEFAULT_PCM_SAMPLE_RATE = 24000

# Request shapes, tried in this order until the API accepts one. The speech
# guide (https://ai.google.dev/gemini-api/docs/speech-generation, read
# 2026-10-18) documents only the Interactions endpoint for the Gemini 3.8
# speech models, but it still mentions generateContent (AUDIO_L16), and
# every earlier Gemini speech model used generateContent. Because a shape
# copied from docs has already been rejected by the live API once in this
# project (see ai/gemini.py), both are supported and the first that works
# is remembered; `ghostcash voice-test` prints which one it was.
_SHAPE_INTERACTIONS = "interactions"
_SHAPE_GENERATE_CONTENT = "generateContent"
_SHAPES = (_SHAPE_INTERACTIONS, _SHAPE_GENERATE_CONTENT)


class _ShapeRejectedError(TTSError):
    """The API refused this request shape (HTTP 400/404) or answered
    without audio - a different request shape may still work."""


def _pcm_to_wav(pcm: bytes, output_path: str, sample_rate: int) -> None:
    if len(pcm) % 2:
        pcm = pcm[:-1]
    with wave.open(str(output_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)


def write_speech_wav(audio: bytes, mime_type: str, output_path: str) -> None:
    """Write audio returned by the API as a WAV file.

    The Interactions API returns a finished WAV (RIFF header) by default;
    generateContent returns bare 16-bit PCM labelled like
    "audio/L16;codec=pcm;rate=24000", which gets a WAV header here.
    """
    if not audio:
        raise TTSError("Gemini returned empty audio.")
    if audio[:4] == b"RIFF" and audio[8:12] == b"WAVE":
        Path(output_path).write_bytes(audio)
        return
    lowered = (mime_type or "").lower()
    if "mulaw" in lowered or "alaw" in lowered:
        raise TTSError(f"Unsupported audio encoding from Gemini: {mime_type!r}")
    match = re.search(r"rate=(\d+)", lowered)
    sample_rate = int(match.group(1)) if match else _DEFAULT_PCM_SAMPLE_RATE
    _pcm_to_wav(audio, output_path, sample_rate)


class GeminiTTSProvider(TTSProvider):
    """Natural-sounding speech from Google's Gemini speech models.

    Free tier: both gemini-3.8-flash-tts and gemini-3.8-flash-lite-tts are
    listed as "Free of charge" on https://ai.google.dev/gemini-api/docs/pricing
    (read 2026-10-18). The free-tier request quota is not published on the
    docs pages - Google AI Studio's rate-limit page shows the live figures
    for your project - so quota exhaustion is expected to happen sometimes
    and is handled by FallbackTTSProvider rather than assumed away.
    """

    API_BASE = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_MODEL = "gemini-3.8-flash-tts"
    DEFAULT_VOICE = "Kore"
    DEFAULT_STYLE = "clear, upbeat and friendly, at a brisk natural pace"
    DEFAULT_TIMEOUT_SECONDS = 120

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        voice: str | None = None,
        style: str | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
        retry_delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL
        self._voice = voice or self.DEFAULT_VOICE
        self._style = self.DEFAULT_STYLE if style is None else style.strip()
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._retry_delays = retry_delays
        self._sleep = sleep
        # The request shape (see _SHAPES) the API has accepted so far.
        self.working_shape: str | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def voice(self) -> str:
        return self._voice

    def check_connectivity(self) -> str:
        """GET models/{model}: metadata only, no quota used. Confirms the
        key works and that this speech model id exists for it."""
        self._require_key()
        url = f"{self.API_BASE}/models/{self._model}"
        try:
            response = self._session.get(
                url, headers={"x-goog-api-key": self._api_key}, timeout=self._timeout_seconds
            )
        except requests.RequestException as exc:
            raise TTSError(f"Could not reach the Gemini API: {exc}") from exc
        if not response.ok:
            raise TTSError(self._format_error(response))
        return f"speech model '{self._model}' is reachable"

    def synthesize(self, text: str, output_path: str) -> None:
        if not text.strip():
            raise TTSError("Cannot synthesize empty text.")
        self._require_key()

        audio, mime_type = self._request_audio(text)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        write_speech_wav(audio, mime_type, output_path)

    # -- request building ---------------------------------------------------

    def _interactions_body(self, text: str) -> dict[str, Any]:
        text_item: dict[str, Any] = {"type": "text", "text": text}
        if self._style:
            text_item["annotations"] = [{"type": "speech_metadata", "style": self._style}]
        return {
            "model": self._model,
            "input": [{"type": "user_input", "content": [text_item]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": self._voice}]},
        }

    def _generate_content_body(self, text: str) -> dict[str, Any]:
        prompt = f"Say in a {self._style} tone: {text}" if self._style else text
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": self._voice}}
                },
            },
        }

    def _request_audio(self, text: str) -> tuple[bytes, str]:
        shapes = (self.working_shape,) if self.working_shape else _SHAPES
        rejections: list[str] = []
        for shape in shapes:
            try:
                if shape == _SHAPE_INTERACTIONS:
                    payload = self._post(
                        f"{self.API_BASE}/interactions", self._interactions_body(text)
                    )
                    audio = self._audio_from_interactions(payload)
                else:
                    url = f"{self.API_BASE}/models/{self._model}:generateContent"
                    payload = self._post(url, self._generate_content_body(text))
                    audio = self._audio_from_generate_content(payload)
            except _ShapeRejectedError as exc:
                rejections.append(f"[{shape}] {exc}")
                continue
            self.working_shape = shape
            return audio

        raise TTSError(
            "Gemini rejected every speech request format GhostCash tried. " + " | ".join(rejections)
        )

    # -- response parsing ---------------------------------------------------

    @staticmethod
    def _audio_from_interactions(payload: dict[str, Any]) -> tuple[bytes, str]:
        found: dict[str, Any] | None = None
        for step in payload.get("steps") or []:
            if not isinstance(step, dict):
                continue
            for item in step.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "audio" and item.get("data"):
                    found = item
        if found is None:
            raise _ShapeRejectedError(
                f"answered without audio (response fields: {sorted(payload)[:8]})"
            )
        return GeminiTTSProvider._decode(found["data"]), str(found.get("mime_type") or "")

    @staticmethod
    def _audio_from_generate_content(payload: dict[str, Any]) -> tuple[bytes, str]:
        try:
            parts = payload["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise _ShapeRejectedError(
                f"answered without audio (response fields: {sorted(payload)[:8]})"
            ) from exc
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline, dict) and inline.get("data"):
                mime = inline.get("mimeType") or inline.get("mime_type") or ""
                return GeminiTTSProvider._decode(inline["data"]), str(mime)
        raise _ShapeRejectedError("answered with text but no audio")

    @staticmethod
    def _decode(data: str) -> bytes:
        try:
            return base64.b64decode(data)
        except (ValueError, TypeError) as exc:
            raise TTSError(f"Gemini returned audio that could not be decoded: {exc}") from exc

    # -- HTTP ---------------------------------------------------------------

    def _require_key(self) -> None:
        if not self._api_key:
            raise TTSError(
                "Gemini speech needs AI_API_KEY (the same key used for text). "
                "Set it in your .env file or as a repository secret."
            )

    @staticmethod
    def _format_error(response: requests.Response) -> str:
        """Google's error body is usually {"error": {"message": ...}}, but
        some failures (seen live: HTTP 400 for a rejected key on the speech
        endpoint) arrive as a JSON *list* of such objects, or as plain
        text - none of which may crash the error report."""
        message = response.text
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, list) and payload:
            payload = payload[0]
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict) and error.get("message"):
                message = str(error["message"])
            elif isinstance(error, str) and error:
                message = error
        return f"Gemini API error {response.status_code}: {message}"

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST with retries for transient failures. HTTP 400/404 raise
        _ShapeRejectedError (another request shape may work); any other
        failure is a plain TTSError."""
        attempt = 0
        while True:
            error: TTSError
            try:
                response = self._session.post(
                    url,
                    headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                error = TTSError(f"Could not reach the Gemini API: {exc}")
                error.__cause__ = exc
                retryable = isinstance(exc, requests.Timeout | requests.ConnectionError)
            else:
                if response.ok:
                    try:
                        payload = response.json()
                    except ValueError as exc:
                        raise TTSError(f"Gemini returned a non-JSON response: {exc}") from exc
                    if not isinstance(payload, dict):
                        raise TTSError(
                            f"Gemini returned an unexpected response ({type(payload).__name__})."
                        )
                    return payload
                if response.status_code in (400, 404):
                    raise _ShapeRejectedError(self._format_error(response))
                error = TTSError(self._format_error(response))
                retryable = response.status_code in _RETRYABLE_STATUS_CODES

            if not retryable or attempt >= len(self._retry_delays):
                raise error
            delay = self._retry_delays[attempt]
            attempt += 1
            logger.warning(
                "Gemini speech request failed (%s). Retrying in %.0f s (retry %d of %d)...",
                error,
                delay,
                attempt,
                len(self._retry_delays),
            )
            self._sleep(delay)


class FallbackTTSProvider(TTSProvider):
    """Use `primary`; if it fails, make the video with `fallback` instead.

    A render should not die because the good voice hit a quota or a
    hiccup - but it must not quietly turn robotic either. Every fallback
    is logged and recorded in `fallback_reasons`, which the render and
    run commands print.
    """

    def __init__(
        self,
        primary: TTSProvider,
        fallback: TTSProvider,
        primary_label: str = "main voice",
        fallback_label: str = "backup voice",
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_label = primary_label
        self._fallback_label = fallback_label
        self.fallback_reasons: list[str] = []

    @property
    def primary(self) -> TTSProvider:
        return self._primary

    def synthesize(self, text: str, output_path: str) -> None:
        try:
            self._primary.synthesize(text, output_path)
            return
        except TTSError as exc:
            reason = str(exc)
        except Exception as exc:  # noqa: BLE001 - a render must survive the main voice breaking
            # Not an expected failure (a bug, an odd response). The video
            # still gets made, and the reason is printed in the warning
            # so the problem is seen, not hidden.
            reason = f"unexpected {type(exc).__name__}: {exc}"
        logger.warning(
            "%s failed (%s); using the %s.", self._primary_label, reason, self._fallback_label
        )
        self.fallback_reasons.append(reason)
        try:
            self._fallback.synthesize(text, output_path)
        except TTSError as exc:
            raise TTSError(f"{exc} (the {self._primary_label} had failed first: {reason})") from exc
