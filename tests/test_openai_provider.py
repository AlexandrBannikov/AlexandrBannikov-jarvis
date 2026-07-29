"""Tests for OpenAIProvider without network access."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from openai import APIConnectionError, APITimeoutError, InternalServerError

from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import (
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMTimeoutError,
)


def test_openai_provider_requires_api_key() -> None:
    provider = OpenAIProvider(api_key="", model="test-model")

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        provider.generate_response("hello")


@patch("app.ai.openai_provider.OpenAI")
def test_openai_provider_uses_responses_api(openai_class: Mock) -> None:
    client = openai_class.return_value
    client.responses.create.return_value = SimpleNamespace(output_text="answer")
    provider = OpenAIProvider(
        api_key="key",
        model="test-model",
        base_url="https://example.test/v1",
    )

    result = provider.generate_response("hello", "be helpful")

    assert result == "answer"
    openai_class.assert_called_once_with(
        api_key="key",
        timeout=30.0,
        max_retries=1,
        base_url="https://example.test/v1",
    )
    client.responses.create.assert_called_once_with(
        model="test-model", input="hello", instructions="be helpful"
    )


@pytest.mark.parametrize(
    ("sdk_error", "expected_error"),
    [
        (APITimeoutError(request=Mock()), LLMTimeoutError),
        (APIConnectionError(request=Mock()), LLMNetworkError),
        (
            InternalServerError(
                "failed",
                response=Mock(status_code=500, headers=Mock()),
                body=None,
            ),
            LLMProviderError,
        ),
    ],
)
def test_openai_provider_translates_sdk_errors(
    sdk_error: Exception, expected_error: type[Exception]
) -> None:
    provider = OpenAIProvider(api_key="key", model="test-model")
    provider._client = Mock()
    provider._client.responses.create.side_effect = sdk_error

    with pytest.raises(expected_error):
        provider.generate_response("hello")
