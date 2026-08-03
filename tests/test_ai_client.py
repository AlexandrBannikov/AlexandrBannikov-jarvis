"""Tests for the provider-independent AI client."""

from unittest.mock import Mock, patch

import pytest

from app.ai.client import AIClient
from app.ai.provider import LLMConfigurationError, LLMProviderError
from app.config import Config


def make_config(provider: str = "openai") -> Config:
    return Config(
        telegram_bot_token="telegram-token",
        llm_provider=provider,
        openai_api_key="openai-key",
        openai_model="test-model",
        openai_base_url=None,
    )


@patch("app.ai.client.OpenAIProvider")
def test_ai_client_selects_openai_provider(provider_class: Mock) -> None:
    provider = provider_class.return_value
    provider.generate_response.return_value = "answer"

    client = AIClient(make_config())

    assert client.ask("question", "system") == "answer"
    provider_class.assert_called_once_with(
        api_key="openai-key", model="test-model", base_url=None,
        timeout=25, max_retries=0,
    )
    provider.generate_response.assert_called_once_with("question", "system")


@patch("app.ai.client.load_config")
@patch("app.ai.client.OpenAIProvider")
def test_ai_client_reads_environment_config(
    provider_class: Mock, load_config: Mock
) -> None:
    load_config.return_value = make_config()

    AIClient()

    load_config.assert_called_once_with()
    provider_class.assert_called_once()


def test_ai_client_rejects_unknown_provider() -> None:
    with pytest.raises(LLMConfigurationError, match="Unsupported"):
        AIClient(make_config("unknown"))


@patch("app.ai.client.OpenAIProvider")
def test_ai_client_propagates_provider_errors(provider_class: Mock) -> None:
    provider_class.return_value.generate_response.side_effect = LLMProviderError(
        "failure"
    )
    client = AIClient(make_config())

    with pytest.raises(LLMProviderError, match="failure"):
        client.ask("question")
