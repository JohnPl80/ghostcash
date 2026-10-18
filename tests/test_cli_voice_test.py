"""`ghostcash voice-test` and the Gemini voice line in `ghostcash health`."""

from __future__ import annotations

import wave
from pathlib import Path

import ghostcash.cli.commands.voice_test as voice_test_cmd
import ghostcash.health as health_module
from ghostcash.cli.main import build_parser, main
from ghostcash.config.schema import Config
from ghostcash.database.connection import get_connection
from ghostcash.database.migrate import apply_migrations
from ghostcash.health import check_system
from ghostcash.video.tts import GeminiTTSProvider, TTSError


def _migrated_db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    conn = get_connection(path)
    apply_migrations(conn)
    conn.close()
    return path


class _FakeGemini(GeminiTTSProvider):
    """Writes a tiny WAV instead of calling the API."""

    def __init__(self, fail: str | None = None, **kwargs) -> None:
        super().__init__(api_key="k", **kwargs)
        self._fail = fail

    def synthesize(self, text: str, output_path: str) -> None:
        if self._fail:
            raise TTSError(self._fail)
        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(24000)
            wav_file.writeframes(b"\x00\x00" * 2400)
        self.working_shape = "interactions"


def test_voice_test_is_registered_with_defaults():
    args = build_parser().parse_args(["voice-test"])
    assert args.command == "voice-test"
    assert args.output == "assets/generated/voice-test.wav"
    assert args.voice is None and args.provider is None


def test_voice_test_with_gemini_writes_a_file_and_reports_the_shape(
    isolated_project: Path, monkeypatch, capsys
):
    seen: dict = {}

    def fake_build(config, voice=None, model=None, style=None):
        seen.update(voice=voice, model=model, style=style)
        return _FakeGemini(voice=voice or "Kore")

    monkeypatch.setattr(voice_test_cmd, "build_gemini_tts", fake_build)
    out = isolated_project / "sample.wav"

    exit_code = main(
        ["voice-test", "--provider", "gemini", "--voice", "Puck", "--output", str(out)]
    )

    text = capsys.readouterr().out
    assert exit_code == 0
    assert out.exists()
    assert seen["voice"] == "Puck"
    assert "voice 'Puck'" in text
    assert "interactions" in text


def test_voice_test_shows_the_real_error_instead_of_hiding_it(
    isolated_project: Path, monkeypatch, capsys
):
    monkeypatch.setattr(
        voice_test_cmd,
        "build_gemini_tts",
        lambda config, voice=None, model=None, style=None: _FakeGemini(fail="quota exceeded"),
    )
    exit_code = main(["voice-test", "--provider", "gemini", "--output", "x.wav"])
    text = capsys.readouterr().out
    assert exit_code == 1
    assert "quota exceeded" in text
    assert not Path("x.wav").exists()


def test_voice_test_flite_works_offline(isolated_project: Path, capsys):
    out = isolated_project / "flite.wav"
    exit_code = main(["voice-test", "--provider", "flite", "--output", str(out)])
    assert exit_code == 0
    assert out.stat().st_size > 0
    assert "flite" in capsys.readouterr().out


def test_voice_test_without_a_key_explains_what_is_missing(
    isolated_project: Path, monkeypatch, capsys
):
    monkeypatch.delenv("AI_API_KEY", raising=False)
    exit_code = main(["voice-test", "--provider", "gemini", "--output", "x.wav"])
    assert exit_code == 1
    assert "AI_API_KEY" in capsys.readouterr().out


# -- health ----------------------------------------------------------------


def test_health_confirms_the_gemini_voice_when_reachable(tmp_path, monkeypatch):
    config = Config()
    config.video.tts_provider = "gemini"
    config.ai.api_key = "fake"
    monkeypatch.setattr(health_module, "get_ai_provider", lambda c: _ok_ai())
    monkeypatch.setattr(health_module, "build_gemini_tts", lambda c: _FakeConn())

    result = check_system(config, str(_migrated_db_path(tmp_path)))

    assert any("Gemini voice" in note and "[ok]" in note for note in result.notes)
    assert result.healthy


def test_health_warns_but_does_not_fail_when_the_gemini_voice_is_unavailable(tmp_path, monkeypatch):
    config = Config()
    config.video.tts_provider = "gemini"
    config.ai.api_key = "fake"
    monkeypatch.setattr(health_module, "get_ai_provider", lambda c: _ok_ai())
    monkeypatch.setattr(
        health_module, "build_gemini_tts", lambda c: _FakeConn(fail="no such model")
    )

    result = check_system(config, str(_migrated_db_path(tmp_path)))

    assert any("[warn]" in note and "no such model" in note for note in result.notes)
    assert not any("Gemini voice" in issue for issue in result.issues)


def test_health_warns_when_gemini_voice_is_configured_without_a_key(tmp_path):
    config = Config()
    config.video.tts_provider = "gemini"
    result = check_system(config, str(_migrated_db_path(tmp_path)))
    assert any("[warn]" in note and "AI_API_KEY" in note for note in result.notes)


def _ok_ai():
    from unittest.mock import MagicMock

    provider = MagicMock()
    provider.check_connectivity.return_value = "reachable"
    return provider


class _FakeConn:
    def __init__(self, fail: str | None = None) -> None:
        self._fail = fail

    def check_connectivity(self) -> str:
        if self._fail:
            raise TTSError(self._fail)
        return "speech model 'x' is reachable"
