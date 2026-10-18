from __future__ import annotations

import base64
import wave
from unittest.mock import MagicMock

import pytest
import requests

from ghostcash.config.schema import Config
from ghostcash.video.renderer import (
    RenderError,
    backup_voice_notice,
    build_gemini_tts,
    flite_required,
    get_tts_provider,
)
from ghostcash.video.tts import (
    FallbackTTSProvider,
    FliteTTSProvider,
    GeminiTTSProvider,
    TTSError,
    TTSProvider,
    write_speech_wav,
)

# The Gemini speech API is an external service, so (CLAUDE.md section 44) it
# is mocked here. A mock cannot prove the live API accepts these request
# shapes - an earlier mocked test suite missed a real HTTP 400 - which is
# why `ghostcash voice-test` exists and reports the shape the API accepted.


def _mock_response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


def _pcm(frames: int = 2400) -> bytes:
    return b"\x01\x00" * frames  # 0.1 s at 24 kHz, 16-bit mono


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _wav_bytes(tmp_path, frames: int = 2400, rate: int = 24000) -> bytes:
    path = tmp_path / "src.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(_pcm(frames))
    return path.read_bytes()


def _interactions_payload(audio_b64: str, mime: str = "audio/wav") -> dict:
    return {
        "steps": [
            {"type": "user_input", "content": [{"type": "text", "text": "hi"}]},
            {
                "type": "model_output",
                "content": [{"type": "audio", "data": audio_b64, "mime_type": mime}],
            },
        ]
    }


def _generate_content_payload(audio_b64: str, mime: str = "audio/L16;codec=pcm;rate=24000") -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"inlineData": {"mimeType": mime, "data": audio_b64}}]}}
        ]
    }


def _provider(session, **kwargs) -> GeminiTTSProvider:
    kwargs.setdefault("retry_delays", ())
    return GeminiTTSProvider(api_key="secret", session=session, **kwargs)


def _wav_info(path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as wav_file:
        return wav_file.getnchannels(), wav_file.getframerate(), wav_file.getnframes()


# -- request / response ---------------------------------------------------


def test_interactions_request_shape_and_wav_passthrough(tmp_path):
    wav = _wav_bytes(tmp_path)
    session = MagicMock()
    session.post.return_value = _mock_response(json_data=_interactions_payload(_b64(wav)))
    out = tmp_path / "nested" / "out.wav"

    provider = _provider(session, voice="Puck", style="calm")
    provider.synthesize("Hello there.", str(out))

    args, kwargs = session.post.call_args
    assert args[0] == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert kwargs["headers"]["x-goog-api-key"] == "secret"
    body = kwargs["json"]
    assert body["model"] == "gemini-3.8-flash-tts"
    assert body["response_format"] == {"type": "audio"}
    assert body["generation_config"] == {"speech_config": [{"voice": "Puck"}]}
    item = body["input"][0]["content"][0]
    assert item["text"] == "Hello there."
    assert item["annotations"] == [{"type": "speech_metadata", "style": "calm"}]
    assert out.read_bytes() == wav
    assert provider.working_shape == "interactions"


def test_empty_style_sends_no_annotation(tmp_path):
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data=_interactions_payload(_b64(_wav_bytes(tmp_path)))
    )
    _provider(session, style="").synthesize("Hi.", str(tmp_path / "o.wav"))
    item = session.post.call_args.kwargs["json"]["input"][0]["content"][0]
    assert "annotations" not in item


def test_raw_pcm_is_wrapped_as_wav_using_the_stated_rate(tmp_path):
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data=_interactions_payload(_b64(_pcm(1600)), mime="audio/l16;rate=16000")
    )
    out = tmp_path / "o.wav"
    _provider(session).synthesize("Hi.", str(out))
    assert _wav_info(out) == (1, 16000, 1600)


def test_falls_back_to_generate_content_shape_when_interactions_is_rejected(tmp_path):
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(404, {"error": {"message": "interactions not found"}}),
        _mock_response(json_data=_generate_content_payload(_b64(_pcm(2400)))),
    ]
    out = tmp_path / "o.wav"
    provider = _provider(session, voice="Charon", style="warm")

    provider.synthesize("Hello.", str(out))

    second_args, second_kwargs = session.post.call_args_list[1]
    assert second_args[0].endswith("/models/gemini-3.8-flash-tts:generateContent")
    body = second_kwargs["json"]
    assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
    voice = body["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]
    assert voice == {"voiceName": "Charon"}
    assert body["contents"][0]["parts"][0]["text"] == "Say in a warm tone: Hello."
    assert _wav_info(out) == (1, 24000, 2400)
    assert provider.working_shape == "generateContent"


def test_working_shape_is_remembered(tmp_path):
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(400, {"error": {"message": "bad"}}),
        _mock_response(json_data=_generate_content_payload(_b64(_pcm()))),
        _mock_response(json_data=_generate_content_payload(_b64(_pcm()))),
    ]
    provider = _provider(session)
    provider.synthesize("One.", str(tmp_path / "a.wav"))
    provider.synthesize("Two.", str(tmp_path / "b.wav"))
    assert session.post.call_count == 3  # second synthesize went straight to generateContent


def test_both_shapes_rejected_shows_every_reason(tmp_path):
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(400, {"error": {"message": "first problem"}}),
        _mock_response(404, {"error": {"message": "second problem"}}),
    ]
    with pytest.raises(TTSError) as excinfo:
        _provider(session).synthesize("Hi.", str(tmp_path / "o.wav"))
    message = str(excinfo.value)
    assert "first problem" in message and "second problem" in message


def test_response_without_audio_tries_next_shape_then_reports(tmp_path):
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(json_data={"steps": [{"type": "model_output", "content": []}]}),
        _mock_response(json_data={"candidates": [{"content": {"parts": [{"text": "words"}]}}]}),
    ]
    with pytest.raises(TTSError, match="without audio"):
        _provider(session).synthesize("Hi.", str(tmp_path / "o.wav"))


def test_auth_failure_is_not_retried_and_not_a_shape_problem(tmp_path):
    session = MagicMock()
    session.post.return_value = _mock_response(403, {"error": {"message": "API key not valid"}})
    sleeps: list[float] = []
    provider = _provider(session, retry_delays=(1.0, 2.0), sleep=sleeps.append)
    with pytest.raises(TTSError, match="API key not valid"):
        provider.synthesize("Hi.", str(tmp_path / "o.wav"))
    assert session.post.call_count == 1
    assert sleeps == []


def test_rate_limit_is_retried_then_succeeds(tmp_path):
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(429, {"error": {"message": "quota"}}),
        _mock_response(503, {"error": {"message": "busy"}}),
        _mock_response(json_data=_interactions_payload(_b64(_wav_bytes(tmp_path)))),
    ]
    sleeps: list[float] = []
    provider = _provider(session, retry_delays=(5.0, 15.0), sleep=sleeps.append)
    provider.synthesize("Hi.", str(tmp_path / "o.wav"))
    assert sleeps == [5.0, 15.0]


def test_rate_limit_gives_up_after_the_retries(tmp_path):
    session = MagicMock()
    session.post.return_value = _mock_response(429, {"error": {"message": "quota exceeded"}})
    provider = _provider(session, retry_delays=(1.0,), sleep=lambda _s: None)
    with pytest.raises(TTSError, match="quota exceeded"):
        provider.synthesize("Hi.", str(tmp_path / "o.wav"))
    assert session.post.call_count == 2


def test_timeout_is_retried(tmp_path):
    session = MagicMock()
    session.post.side_effect = [
        requests.Timeout("slow"),
        _mock_response(json_data=_interactions_payload(_b64(_wav_bytes(tmp_path)))),
    ]
    provider = _provider(session, retry_delays=(1.0,), sleep=lambda _s: None)
    provider.synthesize("Hi.", str(tmp_path / "o.wav"))
    assert session.post.call_count == 2


def test_missing_key_and_empty_text(tmp_path):
    session = MagicMock()
    with pytest.raises(TTSError, match="AI_API_KEY"):
        GeminiTTSProvider(api_key="", session=session).synthesize("Hi.", str(tmp_path / "o.wav"))
    with pytest.raises(TTSError, match="empty"):
        _provider(session).synthesize("   ", str(tmp_path / "o.wav"))
    session.post.assert_not_called()


def test_check_connectivity_uses_metadata_endpoint():
    session = MagicMock()
    session.get.return_value = _mock_response(json_data={"name": "models/gemini-3.8-flash-tts"})
    message = _provider(session).check_connectivity()
    assert "gemini-3.8-flash-tts" in message
    assert session.get.call_args.args[0].endswith("/models/gemini-3.8-flash-tts")


def test_check_connectivity_reports_unknown_model():
    session = MagicMock()
    session.get.return_value = _mock_response(404, {"error": {"message": "model not found"}})
    with pytest.raises(TTSError, match="model not found"):
        _provider(session).check_connectivity()


def test_write_speech_wav_rejects_unsupported_encodings(tmp_path):
    with pytest.raises(TTSError, match="Unsupported"):
        write_speech_wav(b"\x00\x00", "audio/mulaw", str(tmp_path / "o.wav"))
    with pytest.raises(TTSError, match="empty"):
        write_speech_wav(b"", "audio/wav", str(tmp_path / "o.wav"))


# -- fallback -------------------------------------------------------------


class _Recorder(TTSProvider):
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[str] = []
        self._error = error

    def synthesize(self, text: str, output_path: str) -> None:
        self.calls.append(text)
        if self._error:
            raise TTSError(self._error)


def test_fallback_not_used_when_primary_works():
    primary, backup = _Recorder(), _Recorder()
    provider = FallbackTTSProvider(primary, backup)
    provider.synthesize("Hi.", "x.wav")
    assert backup.calls == []
    assert provider.fallback_reasons == []
    assert backup_voice_notice(provider) is None


def test_fallback_used_and_recorded_when_primary_fails():
    primary, backup = _Recorder("quota exceeded"), _Recorder()
    provider = FallbackTTSProvider(primary, backup)
    provider.synthesize("Hi.", "x.wav")
    assert backup.calls == ["Hi."]
    assert provider.fallback_reasons == ["quota exceeded"]
    notice = backup_voice_notice(provider)
    assert notice is not None and "quota exceeded" in notice and "1 narration" in notice


def test_fallback_failure_mentions_both_reasons():
    provider = FallbackTTSProvider(_Recorder("main down"), _Recorder("backup down"))
    with pytest.raises(TTSError) as excinfo:
        provider.synthesize("Hi.", "x.wav")
    assert "backup down" in str(excinfo.value) and "main down" in str(excinfo.value)


# -- configuration wiring -------------------------------------------------


def _config(**video) -> Config:
    config = Config()
    config.ai.api_key = "secret"
    for key, value in video.items():
        setattr(config.video, key, value)
    return config


def test_default_config_is_still_flite():
    provider = get_tts_provider(Config())
    assert isinstance(provider, FliteTTSProvider)


def test_gemini_with_flite_fallback_is_wrapped():
    provider = get_tts_provider(_config(tts_provider="gemini", tts_fallback="flite"))
    assert isinstance(provider, FallbackTTSProvider)
    assert isinstance(provider.primary, GeminiTTSProvider)


def test_gemini_without_fallback_is_bare():
    provider = get_tts_provider(_config(tts_provider="gemini", tts_fallback="none"))
    assert isinstance(provider, GeminiTTSProvider)


def test_unknown_fallback_and_provider_are_clear_errors():
    with pytest.raises(RenderError, match="tts_fallback"):
        get_tts_provider(_config(tts_provider="gemini", tts_fallback="espeak"))
    with pytest.raises(RenderError, match="gemini"):
        get_tts_provider(_config(tts_provider="elevenlabs"))


def test_build_gemini_tts_reads_config_and_overrides():
    config = _config(
        tts_gemini_model="gemini-3.8-flash-lite-tts",
        tts_gemini_voice="Fenrir",
        tts_gemini_style="energetic",
    )
    provider = build_gemini_tts(config)
    assert (provider.model, provider.voice) == ("gemini-3.8-flash-lite-tts", "Fenrir")
    overridden = build_gemini_tts(config, voice="Leda", model="gemini-3.8-flash-tts", style="")
    assert (overridden.model, overridden.voice) == ("gemini-3.8-flash-tts", "Leda")


def test_flite_required_only_when_it_can_be_used():
    assert flite_required(_config(tts_provider="flite")) is True
    assert flite_required(_config(tts_provider="gemini", tts_fallback="flite")) is True
    assert flite_required(_config(tts_provider="gemini", tts_fallback="none")) is False


# -- end to end through the real renderer --------------------------------


def _real_speech_wav(tmp_path) -> bytes:
    """A real spoken WAV (made locally with flite) standing in for the
    audio the API would return."""
    path = tmp_path / "speech.wav"
    FliteTTSProvider().synthesize("This stands in for the voice from the API.", str(path))
    return path.read_bytes()


def _content():
    from ghostcash.models.content import Content

    return Content(
        id=1,
        topic="Excel shortcuts",
        category="EXCEL",
        title="5 Excel tricks",
        hook="You waste hours in Excel.",
        script="Point one is short. Point two is also short.",
        caption="cap",
        cta="Follow now.",
        content_type="INFORMATION",
        status="APPROVED",
    )


def test_render_content_works_with_audio_from_the_gemini_provider(tmp_path):
    from ghostcash.video.renderer import render_content

    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data=_interactions_payload(_b64(_real_speech_wav(tmp_path)))
    )
    config = Config()
    config.video.output_dir = str(tmp_path / "out")

    result = render_content(_content(), config, tts_provider=_provider(session))

    assert result.duration_seconds > 0
    assert (tmp_path / "out" / "content_1.mp4").stat().st_size > 0


def test_render_content_falls_back_to_robotic_voice_and_says_so(tmp_path):
    from ghostcash.video.renderer import render_content

    session = MagicMock()
    session.post.return_value = _mock_response(429, {"error": {"message": "daily quota used up"}})
    provider = FallbackTTSProvider(_provider(session), FliteTTSProvider())
    config = Config()
    config.video.output_dir = str(tmp_path / "out")

    result = render_content(_content(), config, tts_provider=provider)

    assert result.duration_seconds > 0
    assert len(provider.fallback_reasons) == 1
    assert "daily quota used up" in (backup_voice_notice(provider) or "")


# -- odd error bodies (seen live: a rejected key answered with a JSON list) --


def test_error_body_that_is_a_json_list_is_reported_not_crashed_on(tmp_path):
    session = MagicMock()
    session.post.return_value = _mock_response(
        400,
        [{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}],
    )
    with pytest.raises(TTSError, match="API key not valid"):
        _provider(session).synthesize("Hi.", str(tmp_path / "o.wav"))


def test_error_body_that_is_plain_text_or_empty_is_reported(tmp_path):
    session = MagicMock()
    response = _mock_response(500, None, text="upstream exploded")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    provider = _provider(session, retry_delays=())
    with pytest.raises(TTSError, match="upstream exploded"):
        provider.synthesize("Hi.", str(tmp_path / "o.wav"))

    session.post.return_value = _mock_response(503, [], text="")
    with pytest.raises(TTSError, match="503"):
        provider.synthesize("Hi.", str(tmp_path / "o.wav"))


def test_success_body_that_is_not_an_object_is_an_error(tmp_path):
    session = MagicMock()
    session.post.return_value = _mock_response(200, ["unexpected"])
    with pytest.raises(TTSError, match="unexpected response"):
        _provider(session).synthesize("Hi.", str(tmp_path / "o.wav"))


def test_fallback_survives_an_unexpected_error_in_the_main_voice():
    class _Broken(TTSProvider):
        def synthesize(self, text: str, output_path: str) -> None:
            raise AttributeError("boom")

    backup = _Recorder()
    provider = FallbackTTSProvider(_Broken(), backup)
    provider.synthesize("Hi.", "x.wav")
    assert backup.calls == ["Hi."]
    assert "AttributeError" in provider.fallback_reasons[0]
    assert "boom" in provider.fallback_reasons[0]
