"""Tests for OpenAIProvider without network access."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from openai import APIConnectionError, APITimeoutError, InternalServerError

from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMTimeoutError,
    LLMRateLimitError,
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


def test_create_response_supports_current_tool_flow() -> None:
    provider = OpenAIProvider(api_key="key", model="test-model")
    provider._client = Mock()
    expected = SimpleNamespace(id="response-2")
    provider._client.responses.create.return_value = expected
    tools = [{"type": "function", "name": "system_info"}]
    inputs = [
        {
            "type": "function_call_output",
            "call_id": "call-1",
            "output": "{}",
        }
    ]

    actual = provider.create_response(
        inputs,
        tools=tools,
        tool_choice="auto",
        previous_response_id="response-1",
        instructions="safe prompt",
    )

    assert actual is expected
    provider._client.responses.create.assert_called_once_with(
        model="test-model",
        input=inputs,
        stream=False,
        instructions="safe prompt",
        tools=tools,
        tool_choice="auto",
        previous_response_id="response-1",
    )


@pytest.mark.parametrize(
    ("sdk_error_name", "expected_error"),
    [
        ("timeout", LLMTimeoutError),
        ("connection", LLMNetworkError),
        ("rate_limit", LLMRateLimitError),
        ("authentication", LLMAuthenticationError),
    ],
)
def test_create_response_translates_errors(
    sdk_error_name: str, expected_error: type[Exception]
) -> None:
    from openai import AuthenticationError, RateLimitError

    response = Mock(status_code=429, headers=Mock(), request=Mock())
    errors = {
        "timeout": APITimeoutError(request=Mock()),
        "connection": APIConnectionError(request=Mock()),
        "rate_limit": RateLimitError(
            "limited", response=response, body=None
        ),
        "authentication": AuthenticationError(
            "denied", response=response, body=None
        ),
    }
    provider = OpenAIProvider(api_key="key", model="test-model")
    provider._client = Mock()
    provider._client.responses.create.side_effect = errors[sdk_error_name]

    with pytest.raises(expected_error):
        provider.create_response([])


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
