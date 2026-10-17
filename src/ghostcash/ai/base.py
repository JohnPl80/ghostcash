"""AIProvider interface (CLAUDE.md sections 9-10).

Every concrete provider (Gemini today; OpenAI/Anthropic/Ollama planned)
implements this interface so nothing else in the system is hard-coded
to a specific vendor.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProviderError(Exception):
    """Raised for any AI provider failure: network, auth, rate limit,
    or a response that doesn't match what was asked for."""


class AIProvider(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs: Any) -> str:
        """Return a free-text completion for `prompt`."""

    @abstractmethod
    def structured_generate(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object matching the given JSON Schema."""

    @abstractmethod
    def check_connectivity(self) -> str:
        """Confirm the configured API key/model can actually be reached.

        Must use a cheap, read-only call that does not consume generation
        quota/billing (CLAUDE.md section 36: "AI API accessible" is a
        health check, not a content-generation call). Returns a short
        human-readable confirmation string on success, or raises
        AIProviderError on failure.
        """
