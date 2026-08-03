"""Tests for OpenAIProvider without network access."""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from app.ai.openai_provider import (
    DEFAULT_OPENAI_BASE_URL,
    DEFAULT_OPENAI_MODEL,
    OPENAI_MAX_RETRIES,
    OpenAIProvider,
    _exception_chain,
    _is_web_search_unsupported,
)
from app.ai.provider import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMConfigurationError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMPermissionError,
    LLMProviderError,
    LLMTimeoutError,
    LLMRateLimitError,
    LLMWebSearchUnavailableError,
    LLMWebSearchUnsupportedError,
)


def sdk_response(status_code: int) -> Mock:
    return Mock(
        status_code=status_code,
        headers={"x-request-id": f"request-{status_code}"},
        request=Mock(),
    )


def test_exception_chain_contains_only_type_names() -> None:
    inner = OSError("private network details")
    outer = APIConnectionError(request=Mock())
    outer.__cause__ = inner

    result = _exception_chain(outer)

    assert result == "APIConnectionError->OSError"
    assert "private network details" not in result


def test_openai_provider_requires_api_key() -> None:
    provider = OpenAIProvider(api_key="", model="test-model")

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        provider.generate_response("hello")


@patch("app.ai.openai_provider.OpenAI")
def test_empty_environment_base_url_uses_official_endpoint(
    openai_class: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    provider = OpenAIProvider(api_key="key", model="test-model")

    provider._get_client()

    openai_class.assert_called_once_with(
        api_key="key",
        timeout=30.0,
        max_retries=OPENAI_MAX_RETRIES,
        base_url=DEFAULT_OPENAI_BASE_URL,
    )


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
        max_retries=OPENAI_MAX_RETRIES,
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
    ("sdk_error", "expected_error"),
    [
        (
            AuthenticationError(
                "denied", response=sdk_response(401), body=None
            ),
            LLMAuthenticationError,
        ),
        (
            PermissionDeniedError(
                "denied", response=sdk_response(403), body=None
            ),
            LLMPermissionError,
        ),
        (
            BadRequestError(
                "bad request", response=sdk_response(400), body=None
            ),
            LLMBadRequestError,
        ),
        (
            NotFoundError(
                "not found", response=sdk_response(404), body=None
            ),
            LLMModelUnavailableError,
        ),
        (
            RateLimitError(
                "limited", response=sdk_response(429), body=None
            ),
            LLMRateLimitError,
        ),
        (APITimeoutError(request=Mock()), LLMTimeoutError),
        (APIConnectionError(request=Mock()), LLMNetworkError),
        (
            InternalServerError(
                "failed", response=sdk_response(500), body=None
            ),
            LLMProviderError,
        ),
    ],
)
def test_create_response_translates_each_sdk_error(
    sdk_error: Exception, expected_error: type[Exception]
) -> None:
    provider = OpenAIProvider(api_key="key", model=DEFAULT_OPENAI_MODEL)
    provider._client = Mock()
    provider._client.responses.create.side_effect = sdk_error

    with pytest.raises(expected_error):
        provider.create_response([])


@pytest.mark.parametrize(
    "sdk_error",
    [
        PermissionDeniedError(
            "model denied", response=sdk_response(403), body=None
        ),
        NotFoundError(
            "model missing", response=sdk_response(404), body=None
        ),
    ],
)
def test_unavailable_configured_model_falls_back(
    sdk_error: Exception,
) -> None:
    provider = OpenAIProvider(api_key="key", model="unavailable-model")
    provider._client = Mock()
    expected = SimpleNamespace(
        id="response-fallback", _request_id="request-fallback"
    )
    provider._client.responses.create.side_effect = [sdk_error, expected]

    actual = provider.create_response([])

    assert actual is expected
    assert provider.model == DEFAULT_OPENAI_MODEL
    assert (
        provider._client.responses.create.call_args_list[1].kwargs["model"]
        == DEFAULT_OPENAI_MODEL
    )


def test_request_log_contains_metadata_but_not_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider = OpenAIProvider(api_key="key", model="test-model")
    provider._client = Mock()
    provider._client.responses.create.return_value = SimpleNamespace(
        output_text="private response",
        _request_id="request-safe",
    )

    with caplog.at_level("INFO", logger="app.ai.openai_provider"):
        provider.generate_response("private prompt")

    assert "provider=openai model=test-model" in caplog.text
    assert "endpoint=/v1/responses status=200" in caplog.text
    assert "request_id=request-safe" in caplog.text
    assert "private prompt" not in caplog.text
    assert "private response" not in caplog.text


@pytest.mark.parametrize(
    ("sdk_error", "expected_error"),
    [
        (
            BadRequestError(
                "tool unsupported",
                response=sdk_response(400),
                body={
                    "message": "The model does not support web_search.",
                    "type": "invalid_request_error",
                    "param": "tools",
                    "code": "unsupported_web_search",
                },
            ),
            LLMWebSearchUnsupportedError,
        ),
        (
            PermissionDeniedError(
                "tool denied",
                response=sdk_response(403),
                body={
                    "message": "The model does not support web_search.",
                    "type": "invalid_request_error",
                    "param": "tools",
                    "code": "unsupported_web_search",
                },
            ),
            LLMWebSearchUnsupportedError,
        ),
        (
            APITimeoutError(request=Mock()),
            LLMTimeoutError,
        ),
        (
            RateLimitError(
                "limited", response=sdk_response(429), body=None
            ),
            LLMRateLimitError,
        ),
        (
            APIConnectionError(request=Mock()),
            LLMNetworkError,
        ),
    ],
)
def test_web_search_errors_are_distinct(
    sdk_error: Exception, expected_error: type[Exception]
) -> None:
    provider = OpenAIProvider(
        api_key="key", model=DEFAULT_OPENAI_MODEL
    )
    provider._client = Mock()
    provider._client.responses.create.side_effect = sdk_error

    with pytest.raises(expected_error):
        provider.create_response(
            [],
            tools=[{"type": "web_search"}],
            tool_choice="auto",
        )


def test_web_search_requests_retrieved_sources() -> None:
    provider = OpenAIProvider(api_key="key", model=DEFAULT_OPENAI_MODEL)
    provider._client = Mock()
    provider._client.responses.create.return_value = SimpleNamespace(id="r1")

    provider.create_response([], tools=[{"type": "web_search"}])

    assert provider._client.responses.create.call_args.kwargs["include"] == [
        "web_search_call.action.sources"
    ]


def test_invalid_function_schema_is_not_web_search_unsupported() -> None:
    provider = OpenAIProvider(api_key="key", model=DEFAULT_OPENAI_MODEL)
    provider._client = Mock()
    provider._client.responses.create.side_effect = BadRequestError(
        "invalid schema",
        response=sdk_response(400),
        body={
            "message": "Invalid schema for function 'tool'.",
            "type": "invalid_request_error",
            "param": "tools[0].parameters",
            "code": "invalid_function_parameters",
        },
    )

    with pytest.raises(LLMBadRequestError):
        provider.create_response(
            [],
            tools=[
                {"type": "function", "name": "tool"},
                {"type": "web_search"},
            ],
        )


def test_explicit_unsupported_web_search_is_classified() -> None:
    error = BadRequestError(
        "unsupported",
        response=sdk_response(400),
        body={
            "message": "The selected model does not support web_search.",
            "type": "invalid_request_error",
            "param": "tools",
            "code": "unsupported_web_search",
        },
    )
    provider = OpenAIProvider(api_key="key", model=DEFAULT_OPENAI_MODEL)
    provider._client = Mock()
    provider._client.responses.create.side_effect = error

    assert _is_web_search_unsupported(error)
    with pytest.raises(LLMWebSearchUnsupportedError):
        provider.create_response([], tools=[{"type": "web_search"}])


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
