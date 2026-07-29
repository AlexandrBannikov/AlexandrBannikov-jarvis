"""LLM provider interface and provider-independent errors."""

from abc import ABC, abstractmethod


class LLMError(Exception):
    """Base error for LLM operations."""


class LLMConfigurationError(LLMError):
    """The selected provider is not configured correctly."""


class LLMTimeoutError(LLMError):
    """The provider request exceeded its timeout."""


class LLMNetworkError(LLMError):
    """The provider could not be reached."""


class LLMProviderError(LLMError):
    """The provider rejected or failed the request."""


class LLMProvider(ABC):
    """Interface implemented by all LLM providers."""

    @abstractmethod
    def generate_response(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        """Generate a response for a user prompt."""
