from __future__ import annotations

import pytest

from ghostcash.ai.base import AIProviderError
from ghostcash.ai.factory import get_ai_provider
from ghostcash.ai.gemini import GeminiProvider
from ghostcash.config.schema import AIConfig


def test_get_ai_provider_returns_gemini_for_gemini_provider():
    provider = get_ai_provider(AIConfig(provider="gemini", api_key="secret"))
    assert isinstance(provider, GeminiProvider)


def test_get_ai_provider_is_case_insensitive():
    provider = get_ai_provider(AIConfig(provider="GEMINI", api_key="secret"))
    assert isinstance(provider, GeminiProvider)


def test_get_ai_provider_raises_clear_error_for_planned_but_unbuilt_provider():
    with pytest.raises(AIProviderError, match="not implemented yet"):
        get_ai_provider(AIConfig(provider="openai", api_key="secret"))


def test_get_ai_provider_raises_for_unknown_provider():
    with pytest.raises(AIProviderError, match="Unknown AI_PROVIDER"):
        get_ai_provider(AIConfig(provider="totally-made-up", api_key="secret"))
