"""AI provider abstraction (CLAUDE.md sections 9-10).

`get_ai_provider(config.ai)` returns the configured provider (Gemini
today). Everything else in the system should depend only on the
AIProvider interface, never on a concrete provider class.
"""

from ghostcash.ai.base import AIProvider, AIProviderError
from ghostcash.ai.budget import UsageTracker
from ghostcash.ai.factory import get_ai_provider
from ghostcash.ai.gemini import GeminiProvider

__all__ = [
    "AIProvider",
    "AIProviderError",
    "GeminiProvider",
    "UsageTracker",
    "get_ai_provider",
]
