from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from ghostcash.ai.base import AIProviderError
from ghostcash.ai.gemini import GeminiProvider, _to_response_schema


def _mock_response(status_code=200, json_data=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data if json_data is not None else {}
    response.text = text
    return response


def _text_response(text: str, usage: dict | None = None) -> dict:
    payload = {"candidates": [{"content": {"parts": [{"text": text}]}}]}
    if usage:
        payload["usageMetadata"] = usage
    return payload


def test_missing_api_key_raises():
    with pytest.raises(AIProviderError):
        GeminiProvider(api_key="")


def test_generate_posts_to_correct_url_with_auth_header():
    session = MagicMock()
    session.post.return_value = _mock_response(json_data=_text_response("hello"))
    provider = GeminiProvider(api_key="secret", model="gemini-3.8-flash", session=session)

    result = provider.generate("say hi")

    assert result == "hello"
    args, kwargs = session.post.call_args
    assert (
        args[0]
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    )
    assert kwargs["headers"]["x-goog-api-key"] == "secret"
    assert kwargs["json"]["contents"][0]["parts"][0]["text"] == "say hi"


def test_gemini_3_model_omits_temperature_and_sets_thinking_level():
    session = MagicMock()
    session.post.return_value = _mock_response(json_data=_text_response("ok"))
    provider = GeminiProvider(
        api_key="secret", model="gemini-3.8-flash", thinking_level="low", session=session
    )

    provider.generate("hi")

    body = session.post.call_args.kwargs["json"]
    assert "temperature" not in body["generationConfig"]
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "LOW"}


def test_pre_gemini_3_model_sends_temperature_not_thinking_level():
    session = MagicMock()
    session.post.return_value = _mock_response(json_data=_text_response("ok"))
    provider = GeminiProvider(
        api_key="secret", model="gemini-2.5-flash", temperature=0.4, session=session
    )

    provider.generate("hi")

    body = session.post.call_args.kwargs["json"]
    assert body["generationConfig"]["temperature"] == 0.4
    assert "thinkingConfig" not in body["generationConfig"]


def test_structured_generate_uses_response_mime_type_and_json_schema_and_parses_json():
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data=_text_response(
            '{"topics": []}', usage={"promptTokenCount": 5, "candidatesTokenCount": 7}
        )
    )
    provider = GeminiProvider(api_key="secret", session=session)

    schema = {"type": "object", "properties": {"topics": {"type": "array"}}}
    result = provider.structured_generate("give me topics", schema)

    assert result == {"topics": []}
    config = session.post.call_args.kwargs["json"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseJsonSchema"] == schema  # standard JSON Schema, sent unchanged
    # The shape the live API rejected with HTTP 400 must not come back.
    assert "responseFormat" not in config
    assert provider.last_usage == {"prompt_tokens": 5, "output_tokens": 7}


def _structured_400(message: str):
    return _mock_response(status_code=400, json_data={"error": {"message": message}})


def test_structured_generate_falls_back_to_response_schema_on_structured_output_400():
    session = MagicMock()
    session.post.side_effect = [
        _structured_400("Unknown name 'responseJsonSchema' at 'generation_config'"),
        _mock_response(json_data=_text_response('{"a": "x"}')),
    ]
    provider = GeminiProvider(api_key="secret", session=session)
    schema = {"type": "object", "properties": {"a": {"type": ["string", "null"]}}}

    assert provider.structured_generate("p", schema) == {"a": "x"}

    first = session.post.call_args_list[0].kwargs["json"]["generationConfig"]
    second = session.post.call_args_list[1].kwargs["json"]["generationConfig"]
    assert "responseJsonSchema" in first and "responseSchema" not in first
    assert second["responseMimeType"] == "application/json"
    assert second["responseSchema"]["type"] == "OBJECT"
    assert second["responseSchema"]["properties"]["a"] == {"type": "STRING", "nullable": True}
    assert "responseJsonSchema" not in second


def test_structured_generate_remembers_the_shape_that_worked():
    session = MagicMock()
    session.post.side_effect = [
        _structured_400("Invalid value at 'generation_config.response_json_schema'"),
        _mock_response(json_data=_text_response('{"n": 1}')),
        _mock_response(json_data=_text_response('{"n": 2}')),
    ]
    provider = GeminiProvider(api_key="secret", session=session)

    provider.structured_generate("p1", {"type": "object"})
    provider.structured_generate("p2", {"type": "object"})

    # 2 calls for the first request (rejected + accepted), then exactly 1 for
    # the second: it goes straight to the shape that already worked.
    assert session.post.call_count == 3
    third = session.post.call_args_list[2].kwargs["json"]["generationConfig"]
    assert "responseSchema" in third


def test_structured_generate_reports_every_rejection_when_all_shapes_fail():
    session = MagicMock()
    session.post.side_effect = [
        _structured_400("Invalid value at 'generation_config.response_json_schema'"),
        _structured_400("Invalid value at 'generation_config.response_schema'"),
    ]
    provider = GeminiProvider(api_key="secret", session=session)

    with pytest.raises(AIProviderError) as excinfo:
        provider.structured_generate("p", {"type": "object"})

    message = str(excinfo.value)
    assert "[responseJsonSchema]" in message and "[responseSchema]" in message
    assert "response_json_schema" in message and "response_schema" in message


def test_structured_generate_does_not_retry_on_unrelated_400():
    session = MagicMock()
    session.post.return_value = _structured_400("API key not valid. Please pass a valid API key.")
    provider = GeminiProvider(api_key="secret", session=session)

    with pytest.raises(AIProviderError, match="API key not valid"):
        provider.structured_generate("p", {"type": "object"})

    assert session.post.call_count == 1


def test_structured_generate_does_not_try_another_shape_on_non_400_errors():
    session = MagicMock()
    session.post.return_value = _mock_response(
        status_code=403, json_data={"error": {"message": "Permission denied"}}
    )
    provider = GeminiProvider(api_key="secret", session=session)

    with pytest.raises(AIProviderError, match="Permission denied"):
        provider.structured_generate("p", {"type": "object"})

    assert session.post.call_count == 1


def test_structured_generate_retries_503_before_trying_anything_else():
    session = MagicMock()
    session.post.side_effect = [_busy_response(), _mock_response(json_data=_text_response("{}"))]
    provider = GeminiProvider(
        api_key="secret", session=session, retry_delays=(0.0,), sleep=lambda s: None
    )

    assert provider.structured_generate("p", {"type": "object"}) == {}

    # Both attempts used the same (first) request shape: a busy server says
    # nothing about the request format, so the format must not be abandoned.
    shapes = [
        sorted(k for k in c.kwargs["json"]["generationConfig"] if k.startswith("response"))
        for c in session.post.call_args_list
    ]
    assert shapes[0] == shapes[1] == ["responseJsonSchema", "responseMimeType"]


def test_structured_generate_raises_on_invalid_json():
    session = MagicMock()
    session.post.return_value = _mock_response(json_data=_text_response("not json"))
    provider = GeminiProvider(api_key="secret", session=session)

    with pytest.raises(AIProviderError):
        provider.structured_generate("x", {"type": "object"})


def test_http_error_raises_ai_provider_error_with_message():
    session = MagicMock()
    session.post.return_value = _mock_response(
        status_code=403, json_data={"error": {"message": "Permission denied"}}
    )
    provider = GeminiProvider(api_key="secret", session=session)

    with pytest.raises(AIProviderError, match="Permission denied"):
        provider.generate("hi")


def _busy_response():
    return _mock_response(
        status_code=503,
        json_data={"error": {"message": "This model is currently experiencing high demand."}},
    )


def test_transient_503_is_retried_with_the_configured_delays_then_succeeds():
    session = MagicMock()
    session.post.side_effect = [
        _busy_response(),
        _busy_response(),
        _mock_response(json_data=_text_response("finally")),
    ]
    sleeps: list[float] = []
    provider = GeminiProvider(
        api_key="secret", session=session, retry_delays=(1.0, 2.0, 3.0), sleep=sleeps.append
    )

    assert provider.generate("hi") == "finally"
    assert session.post.call_count == 3
    assert sleeps == [1.0, 2.0]


def test_persistent_503_gives_up_after_the_last_retry_with_the_real_message():
    session = MagicMock()
    session.post.return_value = _busy_response()
    sleeps: list[float] = []
    provider = GeminiProvider(
        api_key="secret", session=session, retry_delays=(1.0, 2.0), sleep=sleeps.append
    )

    with pytest.raises(AIProviderError, match="high demand"):
        provider.generate("hi")

    assert session.post.call_count == 3  # first attempt + 2 retries
    assert sleeps == [1.0, 2.0]


def test_rate_limit_429_is_retried():
    session = MagicMock()
    session.post.side_effect = [
        _mock_response(status_code=429, json_data={"error": {"message": "Rate limit exceeded"}}),
        _mock_response(json_data=_text_response("ok")),
    ]
    provider = GeminiProvider(
        api_key="secret", session=session, retry_delays=(0.0,), sleep=lambda s: None
    )

    assert provider.generate("hi") == "ok"
    assert session.post.call_count == 2


@pytest.mark.parametrize(
    "error", [requests.Timeout("Read timed out"), requests.ConnectionError("reset")]
)
def test_timeouts_and_dropped_connections_are_retried(error):
    session = MagicMock()
    session.post.side_effect = [error, _mock_response(json_data=_text_response("ok"))]
    provider = GeminiProvider(
        api_key="secret", session=session, retry_delays=(0.0,), sleep=lambda s: None
    )

    assert provider.generate("hi") == "ok"
    assert session.post.call_count == 2


def test_persistent_timeout_raises_could_not_reach_error():
    session = MagicMock()
    session.post.side_effect = requests.Timeout("Read timed out")
    provider = GeminiProvider(
        api_key="secret", session=session, retry_delays=(0.0,), sleep=lambda s: None
    )

    with pytest.raises(AIProviderError, match="Could not reach the Gemini API"):
        provider.generate("hi")

    assert session.post.call_count == 2


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_not_retried(status):
    session = MagicMock()
    session.post.return_value = _mock_response(
        status_code=status, json_data={"error": {"message": "nope"}}
    )
    sleeps: list[float] = []
    provider = GeminiProvider(api_key="secret", session=session, sleep=sleeps.append)

    with pytest.raises(AIProviderError, match="nope"):
        provider.generate("hi")

    assert session.post.call_count == 1
    assert sleeps == []


def test_unexpected_response_shape_raises_ai_provider_error():
    session = MagicMock()
    session.post.return_value = _mock_response(json_data={"unexpected": True})
    provider = GeminiProvider(api_key="secret", session=session)

    with pytest.raises(AIProviderError):
        provider.generate("hi")


def test_to_response_schema_converts_json_schema_recursively():
    schema = {
        "type": "object",
        "properties": {
            "a": {"type": "string", "enum": ["x", "y"]},
            "n": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
            "b": {
                "type": "array",
                "items": {"type": "object", "properties": {"c": {"type": "number"}}},
            },
        },
        "required": ["a"],
    }
    original = repr(schema)

    result = _to_response_schema(schema)

    assert result["type"] == "OBJECT"
    assert result["propertyOrdering"] == ["a", "n", "b"]
    assert result["required"] == ["a"]
    assert result["properties"]["a"] == {"type": "STRING", "enum": ["x", "y"]}
    assert result["properties"]["n"] == {
        "type": "NUMBER",
        "nullable": True,
        "minimum": 0,
        "maximum": 1,
    }
    assert result["properties"]["b"]["type"] == "ARRAY"
    assert result["properties"]["b"]["items"]["propertyOrdering"] == ["c"]
    assert repr(schema) == original  # the caller's schema is never mutated


def test_error_body_that_is_a_json_list_is_reported_not_crashed_on():
    session = MagicMock()
    session.post.return_value = _mock_response(
        400, [{"error": {"code": 400, "message": "API key not valid."}}]
    )
    provider = GeminiProvider(api_key="secret", session=session, retry_delays=())
    with pytest.raises(AIProviderError, match="API key not valid"):
        provider.generate("hi")


def test_error_body_that_is_plain_text_is_reported():
    session = MagicMock()
    response = _mock_response(500, None, text="upstream exploded")
    response.json.side_effect = ValueError("not json")
    session.post.return_value = response
    provider = GeminiProvider(api_key="secret", session=session, retry_delays=())
    with pytest.raises(AIProviderError, match="upstream exploded"):
        provider.generate("hi")
