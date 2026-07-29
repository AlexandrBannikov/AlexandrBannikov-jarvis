"""OpenAI implementation of the LLM provider interface."""

import logging
import time

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    OpenAI,
)

from app.ai.provider import (
    LLMConfigurationError,
    LLMNetworkError,
    LLMProvider,
    LLMProviderError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    """Generate responses through the current OpenAI Responses API."""

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self._client: OpenAI | None = None
        logger.info("Configured OpenAI provider with model=%s", model)

    def _get_client(self) -> OpenAI:
        if not self.api_key:
            raise LLMConfigurationError("OPENAI_API_KEY is required")
        if self._client is None:
            options: dict[str, object] = {
                "api_key": self.api_key,
                "timeout": self.timeout,
                "max_retries": 1,
            }
            if self.base_url:
                options["base_url"] = self.base_url
            self._client = OpenAI(**options)
        return self._client

    def generate_response(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        started_at = time.monotonic()
        try:
            request: dict[str, object] = {
                "model": self.model,
                "input": prompt,
            }
            if system_prompt:
                request["instructions"] = system_prompt
            response = self._get_client().responses.create(**request)
            return response.output_text
        except LLMConfigurationError:
            logger.error("OpenAI provider is missing its API key")
            raise
        except APITimeoutError as error:
            logger.warning("OpenAI request timed out")
            raise LLMTimeoutError("OpenAI request timed out") from error
        except APIConnectionError as error:
            logger.warning("OpenAI network error: %s", type(error).__name__)
            raise LLMNetworkError("Could not connect to OpenAI") from error
        except APIError as error:
            logger.error("OpenAI API error: %s", type(error).__name__)
            raise LLMProviderError("OpenAI API request failed") from error
        except Exception:
            logger.exception("Unexpected OpenAI provider error")
            raise
        finally:
            logger.info(
                "OpenAI request completed in %.3f seconds",
                time.monotonic() - started_at,
            )
