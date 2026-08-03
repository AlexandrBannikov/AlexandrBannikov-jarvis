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


class LLMRateLimitError(LLMError):
    """The provider rate limit was reached."""


class LLMQuotaError(LLMError):
    """The provider account has no usable quota."""


class LLMAuthenticationError(LLMError):
    """The provider rejected configured credentials."""


class LLMPermissionError(LLMError):
    """The provider denied access to the requested operation."""


class LLMBadRequestError(LLMError):
    """The provider rejected the request parameters."""


class LLMModelUnavailableError(LLMError):
    """The requested model is unavailable to this account."""


class LLMWebSearchUnavailableError(LLMError):
    """The hosted web search tool is temporarily unavailable."""


class LLMWebSearchUnsupportedError(LLMError):
    """The selected model does not support hosted web search."""


class LLMProviderError(LLMError):
    """The provider rejected or failed the request."""


class LLMProvider(ABC):
    """Interface implemented by all LLM providers."""

    @abstractmethod
    def generate_response(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        """Generate a response for a user prompt."""
