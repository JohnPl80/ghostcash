"""AIProvider factory (CLAUDE.md section 9).

Keeps the rest of the system decoupled from any one vendor: callers ask
for "the configured provider" and get back something implementing
AIProvider, without knowing or caring which one it is.
"""

from __future__ import annotations

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.gemini import GeminiProvider
from ghostcash.config.schema import AIConfig

# Recognized in AI_PROVIDER but not built yet - gives a clear error
# instead of "unknown provider" when someone reasonably expects it to work.
_PLANNED_PROVIDERS = {"openai", "anthropic", "ollama"}


def get_ai_provider(config: AIConfig) -> AIProvider:
    provider_name = (config.provider or "").strip().lower()

    if provider_name == "gemini":
        return GeminiProvider(
            api_key=config.api_key,
            model=config.model or None,
            temperature=config.temperature,
            thinking_level=config.thinking_level,
        )

    if provider_name in _PLANNED_PROVIDERS:
        raise AIProviderError(
            f"AI_PROVIDER={provider_name!r} is recognized but not implemented yet. "
            "Currently implemented: gemini."
        )

    raise AIProviderError(f"Unknown AI_PROVIDER: {provider_name!r}. Currently implemented: gemini.")
