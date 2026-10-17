from __future__ import annotations

import subprocess

import pytest

from ghostcash.video.tts import FliteTTSProvider, TTSError, is_flite_available

# These exercise the real ffmpeg/flite filter rather than mocking it -
# it's a local, deterministic, free tool (like ffmpeg itself), not an
# external API, so CLAUDE.md section 44's "mock external APIs" doesn't
# apply, and a mock would just be testing the mock.


def test_is_flite_available_reflects_real_environment():
    # This environment's ffmpeg was confirmed to have libflite support
    # while building this feature; if that's ever not true elsewhere,
    # this function should honestly report False rather than crash.
    assert is_flite_available() is True


def test_is_flite_available_returns_false_for_bad_binary():
    assert is_flite_available(ffmpeg_binary="/no/such/ffmpeg") is False


def test_synthesize_produces_a_playable_wav(tmp_path):
    output_path = tmp_path / "out.wav"
    provider = FliteTTSProvider()

    provider.synthesize("Hello, this is a test.", str(output_path))

    assert output_path.exists()
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(output_path),
        ],
        capture_output=True,
        text=True,
    )
    duration = float(result.stdout.strip())
    assert duration > 0


def test_synthesize_handles_punctuation_safely(tmp_path):
    # Historically the tricky part: colons/apostrophes/dollar signs are
    # structural characters in ffmpeg's filtergraph syntax. textfile=
    # should sidestep all of that.
    output_path = tmp_path / "out.wav"
    provider = FliteTTSProvider()
    provider.synthesize("Don't forget: this costs $5, right? Let's see...", str(output_path))
    assert output_path.exists()
    assert output_path.stat().st_size > 0


def test_synthesize_rejects_empty_text(tmp_path):
    provider = FliteTTSProvider()
    with pytest.raises(TTSError):
        provider.synthesize("   ", str(tmp_path / "out.wav"))


def test_synthesize_raises_clear_error_for_missing_ffmpeg(tmp_path):
    provider = FliteTTSProvider(ffmpeg_binary="/no/such/ffmpeg")
    with pytest.raises(TTSError, match="not found"):
        provider.synthesize("Hello", str(tmp_path / "out.wav"))


def test_synthesize_creates_parent_directory(tmp_path):
    nested_path = tmp_path / "nested" / "dir" / "out.wav"
    provider = FliteTTSProvider()
    provider.synthesize("Hello world", str(nested_path))
    assert nested_path.exists()
