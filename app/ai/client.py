"""Provider-independent client used by the application."""

import logging
import time

from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import LLMConfigurationError, LLMProvider
from app.config import Config, load_config

logger = logging.getLogger(__name__)


class AIClient:
    """Select an LLM provider and expose a stable application interface."""

    def __init__(self, config: Config | None = None) -> None:
        config = config or load_config()
        provider_name = config.llm_provider.lower()
        logger.info("Starting AI client with provider=%s", provider_name)
        self.provider = self._create_provider(provider_name, config)

    @staticmethod
    def _create_provider(provider_name: str, config: Config) -> LLMProvider:
        if provider_name == "openai":
            return OpenAIProvider(
                api_key=config.openai_api_key,
                model=config.openai_model,
                base_url=config.openai_base_url,
                timeout=config.openai_request_timeout_seconds,
                max_retries=0,
            )
        logger.error("Unsupported LLM provider: %s", provider_name)
        raise LLMConfigurationError(
            f"Unsupported LLM provider: {provider_name}"
        )

    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        """Ask the selected provider without exposing its implementation."""
        started_at = time.monotonic()
        try:
            return self.provider.generate_response(prompt, system_prompt)
        finally:
            logger.info(
                "LLM request duration: %.3f seconds",
                time.monotonic() - started_at,
            )
