"""Google Gemini implementation of AIProvider.

Implemented against the classic, stateless `generateContent` REST endpoint.
Google's newer "Interactions API" (stateful, agent-oriented) is now the
recommended entry point for *new* projects, but Google states
generateContent "remains fully supported" - and it's the better fit here:
GhostCash's calls (generate a topic batch, generate a script) are
one-shot, not multi-turn conversations, so there's no state worth paying
for. If that changes, swap this file - callers only see AIProvider.

Reference (last verified 2026-09-08 against docs dated 2026-09-02):
  https://ai.google.dev/api
  https://ai.google.dev/gemini-api/docs/generate-content/structured-output

Notable, easy-to-miss detail this implementation gets right: Gemini 3.x
models (the current default) deprecated temperature/top_p/top_k in favor
of `thinking_level` (low/medium/high). Sending temperature to a 3.x model
is silently ignored, not an error - but silently doing nothing is exactly
the kind of thing that should be handled explicitly rather than copied
from stale examples, so this provider only sends the parameter the
selected model actually respects.

Structured output - a correction learned the hard way (2026-10-15): the
structured-output guide documents `generationConfig.responseFormat.text`
(mimeType "application/json" + schema), and an earlier version of this
file sent exactly that. The live API rejected it with HTTP 400 ("Invalid
value at 'generation_config.response_format.text.mime_type' ...
'application/json'"): the field exists, but its mime-type enum does not
accept that string. The generateContent API reference's own REST samples
use the long-standing `generationConfig.responseMimeType` plus a schema
field, so that is what this file sends now. Two schema fields exist and
GhostCash's schemas use JSON-Schema features (`"type": ["string",
"null"]`, enum, minimum/maximum), so they are tried in this order:

  1. responseJsonSchema - takes standard JSON Schema as-is.
  2. responseSchema     - the older OpenAPI-subset `Schema`; the schema is
                          converted (upper-case types, `nullable`,
                          `propertyOrdering`) before sending.

If the API answers HTTP 400 and the message is about the structured-output
fields, the next shape is tried; the first one that works is remembered
for the rest of the process. If every shape is rejected, the error shows
each rejection so the real cause is visible instead of a generic failure.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import requests

from ghostcash.ai.base import AIProvider, AIProviderError

logger = logging.getLogger(__name__)

_GEMINI_3_PREFIX = "gemini-3"

# Transient failures worth retrying: Google's "model is experiencing high
# demand" (503) is explicitly described as temporary, and a read timeout on
# a busy model usually clears on the next attempt. Seen in a real first run
# (2026-10-15): a 503, then a 60 s read timeout. Anything else (400, 401,
# 403, 404 ...) is a problem retrying cannot fix and is raised immediately.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Seconds to wait before each retry: 3 retries, ~65 s of waiting in total.
_DEFAULT_RETRY_DELAYS: tuple[float, ...] = (5.0, 15.0, 45.0)

# Words that mark a 400 as being about the structured-output request fields
# (as opposed to, say, an invalid API key, which must not trigger a retry).
_STRUCTURED_OUTPUT_ERROR_HINTS = (
    "response",
    "mime",
    "schema",
    "generation_config",
    "generationconfig",
)
_STRUCTURED_SHAPES = ("responseJsonSchema", "responseSchema")


class _BadRequestError(AIProviderError):
    """HTTP 400 from the Gemini API (kept separate so structured_generate
    can decide whether a different request shape is worth trying)."""


def _is_gemini_3_family(model: str) -> bool:
    return model.startswith(_GEMINI_3_PREFIX)


def _to_response_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a JSON Schema into the OpenAPI-subset `Schema` that the
    older `responseSchema` field expects: a single upper-case `type`
    (JSON Schema's `["string", "null"]` becomes `STRING` + `nullable`),
    recursive `properties`/`items`, and `propertyOrdering`. Keywords that
    both formats share (enum, required, minimum, maximum, description)
    pass through unchanged."""
    node = dict(schema)

    type_value = node.get("type")
    if isinstance(type_value, list):
        non_null = [t for t in type_value if t != "null"]
        if len(non_null) != len(type_value):
            node["nullable"] = True
        type_value = non_null[0] if non_null else "string"
    if isinstance(type_value, str):
        node["type"] = type_value.upper()

    properties = node.get("properties")
    if isinstance(properties, dict):
        node["properties"] = {key: _to_response_schema(value) for key, value in properties.items()}
        node.setdefault("propertyOrdering", list(properties.keys()))
    items = node.get("items")
    if isinstance(items, dict):
        node["items"] = _to_response_schema(items)
    return node


def _structured_generation_config(shape: str, schema: dict[str, Any]) -> dict[str, Any]:
    if shape == "responseJsonSchema":
        return {"responseMimeType": "application/json", "responseJsonSchema": schema}
    return {"responseMimeType": "application/json", "responseSchema": _to_response_schema(schema)}


class GeminiProvider(AIProvider):
    API_BASE = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_MODEL = "gemini-3.8-flash"
    DEFAULT_TIMEOUT_SECONDS = 90

    def __init__(
        self,
        api_key: str,
        model: str | None = None,
        temperature: float = 0.7,
        thinking_level: str = "low",
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        session: requests.Session | None = None,
        retry_delays: tuple[float, ...] = _DEFAULT_RETRY_DELAYS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise AIProviderError(
                "Gemini API key is required. Set AI_API_KEY in your .env "
                "(get one at https://aistudio.google.com/apikey)."
            )
        self._api_key = api_key
        self._model = model or self.DEFAULT_MODEL
        self._temperature = temperature
        self._thinking_level = thinking_level
        self._timeout_seconds = timeout_seconds
        self._session = session or requests.Session()
        self._retry_delays = retry_delays
        self._sleep = sleep
        # Populated after each successful call from the response's
        # usageMetadata, if present - callers can feed this into
        # ai.budget.UsageTracker. None until the first call.
        self.last_usage: dict[str, int] | None = None
        # The structured-output request shape (see _STRUCTURED_SHAPES) that
        # the API has accepted so far; None until one succeeds.
        self._working_structured_shape: str | None = None

    def generate(self, prompt: str, **kwargs: Any) -> str:
        response = self._call(prompt, structured_config=None)
        return self._extract_text(response)

    def check_connectivity(self) -> str:
        """GET models/{model} - metadata only, no generation quota consumed
        (verified against https://ai.google.dev/api/models, 2026-10-07).
        Confirms both that the API key authenticates and that the
        configured model id actually exists."""
        url = f"{self.API_BASE}/models/{self._model}"
        try:
            response = self._session.get(
                url,
                headers={"x-goog-api-key": self._api_key},
                timeout=self._timeout_seconds,
            )
        except requests.RequestException as exc:
            raise AIProviderError(f"Could not reach the Gemini API: {exc}") from exc

        if not response.ok:
            raise AIProviderError(self._format_error(response))

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise AIProviderError(f"Gemini returned a non-JSON response: {exc}") from exc

        display_name = payload.get("displayName") or self._model
        return f"model '{display_name}' is reachable"

    def structured_generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        response = self._call_structured(prompt, schema)
        text = self._extract_text(response)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIProviderError(f"Gemini returned output that wasn't valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise AIProviderError(
                f"Expected a JSON object from Gemini, got: {type(parsed).__name__}"
            )
        return parsed

    def _call_structured(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Send a structured-output request, trying each known request
        shape until the API accepts one (see the module docstring)."""
        if self._working_structured_shape is not None:
            shapes: tuple[str, ...] = (self._working_structured_shape,)
        else:
            shapes = _STRUCTURED_SHAPES

        rejections: list[str] = []
        for shape in shapes:
            try:
                response = self._call(prompt, _structured_generation_config(shape, schema))
            except _BadRequestError as exc:
                if not any(hint in str(exc).lower() for hint in _STRUCTURED_OUTPUT_ERROR_HINTS):
                    raise
                rejections.append(f"[{shape}] {exc}")
                continue
            self._working_structured_shape = shape
            return response

        raise AIProviderError(
            "Gemini rejected every structured-output request format GhostCash tried. "
            + " | ".join(rejections)
        )

    def _post_with_retries(self, url: str, body: dict[str, Any]) -> requests.Response:
        """POST `body`, retrying transient failures (see
        _RETRYABLE_STATUS_CODES, plus timeouts and dropped connections)
        after each delay in self._retry_delays. Returns the first
        successful response; raises AIProviderError for a non-retryable
        failure, or once the retries run out."""
        attempt = 0
        while True:
            error: AIProviderError
            try:
                response = self._session.post(
                    url,
                    headers={"x-goog-api-key": self._api_key, "Content-Type": "application/json"},
                    json=body,
                    timeout=self._timeout_seconds,
                )
            except requests.RequestException as exc:
                error = AIProviderError(f"Could not reach the Gemini API: {exc}")
                error.__cause__ = exc
                retryable = isinstance(exc, requests.Timeout | requests.ConnectionError)
            else:
                if response.ok:
                    return response
                error_class = _BadRequestError if response.status_code == 400 else AIProviderError
                error = error_class(self._format_error(response))
                retryable = response.status_code in _RETRYABLE_STATUS_CODES

            if not retryable or attempt >= len(self._retry_delays):
                raise error
            delay = self._retry_delays[attempt]
            attempt += 1
            logger.warning(
                "Gemini request failed (%s). Retrying in %.0f s (retry %d of %d)...",
                error,
                delay,
                attempt,
                len(self._retry_delays),
            )
            self._sleep(delay)

    def _call(self, prompt: str, structured_config: dict[str, Any] | None) -> dict[str, Any]:
        generation_config: dict[str, Any] = {}

        if _is_gemini_3_family(self._model):
            if self._thinking_level:
                generation_config["thinkingConfig"] = {
                    "thinkingLevel": self._thinking_level.upper()
                }
        else:
            generation_config["temperature"] = self._temperature

        if structured_config is not None:
            generation_config.update(structured_config)

        body: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}]}
        if generation_config:
            body["generationConfig"] = generation_config

        url = f"{self.API_BASE}/models/{self._model}:generateContent"
        response = self._post_with_retries(url, body)

        try:
            payload: dict[str, Any] = response.json()
        except ValueError as exc:
            raise AIProviderError(f"Gemini returned a non-JSON response: {exc}") from exc

        usage = payload.get("usageMetadata")
        if isinstance(usage, dict):
            self.last_usage = {
                "prompt_tokens": int(usage.get("promptTokenCount", 0)),
                "output_tokens": int(usage.get("candidatesTokenCount", 0)),
            }

        return payload

    @staticmethod
    def _format_error(response: requests.Response) -> str:
        """Google's error body is usually {"error": {"message": ...}}, but
        can arrive as a JSON list of such objects or as plain text; none of
        those may crash the error report (which would hide the real error)."""
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

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        try:
            candidates = response["candidates"]
            parts = candidates[0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError(f"Unexpected Gemini response shape: {response!r}") from exc
