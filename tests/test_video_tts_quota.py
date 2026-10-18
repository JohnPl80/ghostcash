"""Gemini speech free-tier behaviour: 10 requests/day, 3 requests/minute.

The 429 texts below are the real ones Google returned on 2026-10-18.
"""

from __future__ import annotations

import base64
import wave
from unittest.mock import MagicMock

import pytest
import requests

from ghostcash.video.tts import (
    FallbackTTSProvider,
    FliteTTSProvider,
    GeminiTTSProvider,
    TTSError,
    TTSProvider,
    TTSQuotaExhaustedError,
    TTSTemporaryError,
    _retry_in_seconds,
)

DAILY_429 = (
    "Rate limit exceeded for model gemini-3.8-flash-tts (limit: 10 requests per day on "
    "Free Tier). Please retry in 8h15m26s or upgrade your tier at https://ai.dev/rate-limit."
)
MINUTE_429 = (
    "Rate limit exceeded for model gemini-3.8-flash-tts (limit: 3 requests per minute on "
    "Free Tier). Please retry in 20s or upgrade your tier at https://ai.dev/rate-limit."
)


def _response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


def _error(status_code, message):
    return _response(status_code, {"error": {"code": status_code, "message": message}})


def _good_response(tmp_path):
    path = tmp_path / "src.wav"
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x01\x00" * 2400)
    data = base64.b64encode(path.read_bytes()).decode()
    return _response(
        json_data={
            "steps": [
                {"content": [{"type": "audio", "data": data, "mime_type": "audio/wav"}]},
            ]
        }
    )


def _provider(session, sleeps=None, **kwargs) -> GeminiTTSProvider:
    sleeps = sleeps if sleeps is not None else []
    kwargs.setdefault("retry_delays", (5, 15, 45))
    return GeminiTTSProvider(api_key="secret", session=session, sleep=sleeps.append, **kwargs)


# -- reading Google's "retry in" hint --------------------------------------


def test_retry_hint_is_parsed():
    assert _retry_in_seconds("Please retry in 20s or upgrade") == 20
    assert _retry_in_seconds("Please retry in 8h15m26s or") == 8 * 3600 + 15 * 60 + 26
    assert _retry_in_seconds("retry in 1m30s") == 90
    assert _retry_in_seconds("retry in 2.5s") == 2.5
    assert _retry_in_seconds("no hint here") is None
    assert _retry_in_seconds("retry in soon") is None


# -- daily allowance used up ----------------------------------------------


def test_daily_limit_stops_at_once_without_retrying(tmp_path):
    session = MagicMock()
    session.post.return_value = _error(429, DAILY_429)
    sleeps: list[float] = []

    with pytest.raises(TTSQuotaExhaustedError, match="10 requests per day"):
        _provider(session, sleeps).synthesize("Hello.", str(tmp_path / "o.wav"))

    assert session.post.call_count == 1  # no retries: they cannot help
    assert sleeps == []


def test_after_the_daily_limit_later_calls_fail_without_a_request(tmp_path):
    session = MagicMock()
    session.post.return_value = _error(429, DAILY_429)
    provider = _provider(session)
    with pytest.raises(TTSQuotaExhaustedError):
        provider.synthesize("One.", str(tmp_path / "a.wav"))

    with pytest.raises(TTSQuotaExhaustedError, match="per day"):
        provider.synthesize("Two.", str(tmp_path / "b.wav"))

    assert session.post.call_count == 1
    assert provider.exhausted_reason and "per day" in provider.exhausted_reason


def test_quota_error_is_a_temporary_error_and_a_tts_error():
    assert issubclass(TTSQuotaExhaustedError, TTSTemporaryError)
    assert issubclass(TTSTemporaryError, TTSError)


# -- per-minute limit ------------------------------------------------------


def test_per_minute_limit_waits_the_time_google_asks_then_succeeds(tmp_path):
    session = MagicMock()
    session.post.side_effect = [_error(429, MINUTE_429), _good_response(tmp_path)]
    sleeps: list[float] = []
    out = tmp_path / "o.wav"

    _provider(session, sleeps).synthesize("Hello.", str(out))

    assert out.exists()
    assert session.post.call_count == 2
    assert sleeps == [21.0]  # "retry in 20s" + 1 s margin, not the fixed 5 s


def test_a_hint_longer_than_worth_waiting_gives_up_as_temporary(tmp_path):
    session = MagicMock()
    session.post.return_value = _error(429, "limit: 3 requests per minute. Please retry in 500s")
    sleeps: list[float] = []

    with pytest.raises(TTSTemporaryError):
        _provider(session, sleeps).synthesize("Hello.", str(tmp_path / "o.wav"))

    assert session.post.call_count == 1
    assert sleeps == []


# -- other failures: temporary vs permanent --------------------------------


def test_persistent_server_error_is_temporary_after_retries(tmp_path):
    session = MagicMock()
    session.post.return_value = _error(503, "The model is overloaded.")
    sleeps: list[float] = []

    with pytest.raises(TTSTemporaryError, match="overloaded"):
        _provider(session, sleeps).synthesize("Hello.", str(tmp_path / "o.wav"))

    assert session.post.call_count == 4  # first try + 3 retries
    assert sleeps == [5, 15, 45]


def test_network_trouble_is_temporary(tmp_path):
    session = MagicMock()
    session.post.side_effect = requests.ConnectionError("no route")

    with pytest.raises(TTSTemporaryError, match="Could not reach"):
        _provider(session, retry_delays=()).synthesize("Hello.", str(tmp_path / "o.wav"))


def test_a_rejected_key_is_permanent_not_temporary(tmp_path):
    session = MagicMock()
    session.post.return_value = _error(403, "API key not valid.")

    with pytest.raises(TTSError) as excinfo:
        _provider(session, retry_delays=()).synthesize("Hello.", str(tmp_path / "o.wav"))

    assert not isinstance(excinfo.value, TTSTemporaryError)
    assert session.post.call_count == 1


# -- pacing ----------------------------------------------------------------


def test_requests_are_spaced_out(tmp_path):
    session = MagicMock()
    session.post.side_effect = lambda *a, **k: _good_response(tmp_path)
    now = [1000.0]
    sleeps: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    provider = GeminiTTSProvider(
        api_key="secret",
        session=session,
        sleep=fake_sleep,
        clock=lambda: now[0],
        min_interval_seconds=21,
    )
    provider.synthesize("One.", str(tmp_path / "a.wav"))
    assert sleeps == []  # the first request never waits

    now[0] += 5  # five seconds of other work
    provider.synthesize("Two.", str(tmp_path / "b.wav"))
    assert sleeps == [16.0]

    now[0] += 60  # long enough ago: no wait
    provider.synthesize("Three.", str(tmp_path / "c.wav"))
    assert sleeps == [16.0]


def test_no_pacing_by_default(tmp_path):
    session = MagicMock()
    session.post.side_effect = lambda *a, **k: _good_response(tmp_path)
    sleeps: list[float] = []
    provider = _provider(session, sleeps)
    provider.synthesize("One.", str(tmp_path / "a.wav"))
    provider.synthesize("Two.", str(tmp_path / "b.wav"))
    assert sleeps == []


# -- cache identity ---------------------------------------------------------


def test_cache_identity_changes_with_voice_model_and_style():
    base = GeminiTTSProvider(api_key="k", model="m1", voice="Kore", style="calm")
    same = GeminiTTSProvider(api_key="other-key", model="m1", voice="Kore", style="calm")
    assert base.cache_identity == same.cache_identity  # the key is not part of it
    for changed in (
        GeminiTTSProvider(api_key="k", model="m2", voice="Kore", style="calm"),
        GeminiTTSProvider(api_key="k", model="m1", voice="Puck", style="calm"),
        GeminiTTSProvider(api_key="k", model="m1", voice="Kore", style="loud"),
    ):
        assert changed.cache_identity != base.cache_identity


def test_flite_is_not_cached_and_the_fallback_wrapper_delegates():
    assert FliteTTSProvider().cache_identity is None
    gemini = GeminiTTSProvider(api_key="k")
    wrapper = FallbackTTSProvider(gemini, FliteTTSProvider())
    assert wrapper.cache_identity == gemini.cache_identity


# -- which voice made the audio ---------------------------------------------


class _Failing(TTSProvider):
    def synthesize(self, text: str, output_path: str) -> None:
        raise TTSQuotaExhaustedError("used up")


class _Working(TTSProvider):
    def synthesize(self, text: str, output_path: str) -> None:
        open(output_path, "wb").close()


def test_fallback_wrapper_says_whether_the_backup_voice_was_used(tmp_path):
    wrapper = FallbackTTSProvider(_Failing(), _Working())
    wrapper.synthesize("Hi.", str(tmp_path / "a.wav"))
    assert wrapper.used_fallback_last is True

    healthy = FallbackTTSProvider(_Working(), _Working())
    healthy.synthesize("Hi.", str(tmp_path / "b.wav"))
    assert healthy.used_fallback_last is False
