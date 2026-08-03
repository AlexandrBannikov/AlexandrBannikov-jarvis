"""Focused regression matrix for weather search reliability and safe tracing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.ai.agent import JarvisAgent
from app.ai.openai_provider import _safe_correlation_id
from app.ai.provider import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMProviderError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from app.config import load_config
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


async def immediate(function, *args, **kwargs):
    return function(*args, **kwargs)


class Provider:
    def __init__(self, result):
        self.result = result
        self.requests = []

    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def agent(provider, location=None):
    return JarvisAgent(
        provider,
        ToolManager(ToolRegistry()),
        run_sync=immediate,
        web_search_enabled=True,
        location_service=location,
    )


def web_result():
    return SimpleNamespace(
        id="r1",
        output_text="Актуальный прогноз.",
        output=[
            {"type": "web_search_call", "status": "completed"},
            {"type": "message", "content": [{"type": "output_text", "annotations": [
                {"type": "url_citation", "title": "Источник", "url": "https://example.com/w"}
            ]}]},
        ],
    )


@pytest.mark.parametrize(
    ("left", "right", "same"),
    [
        ("{}", "{ }", True),
        ('{"a":1,"b":2}', '{"b":2,"a":1}', True),
        ('{"a":1}', '{"a":2}', False),
        ('{"a":[1,2]}', '{"a":[1,2]}', True),
        ('{"a":[1,2]}', '{"a":[2,1]}', False),
        ('{"nested":{"b":2,"a":1}}', '{"nested":{"a":1,"b":2}}', True),
        ('{"s":"x"}', '{"s":" x "}', False),
        ("bad", " bad ", True),
        ('{"flag":true}', '{"flag":false}', False),
        ('{"n":1.0}', '{"n":1}', False),
    ],
)
def test_tool_fingerprint_normalization(left, right, same):
    call1 = {"name": "get", "arguments": left}
    call2 = {"name": " GET ", "arguments": right}
    assert (JarvisAgent._tool_fingerprint(call1) == JarvisAgent._tool_fingerprint(call2)) is same


@pytest.mark.parametrize(
    "question",
    [
        "Как погода?",
        "Какая погода сегодня?",
        "Будет дождь?",
        "Что надеть по погоде?",
        "Прогноз погоды на завтра",
    ],
)
def test_saved_location_weather_is_single_hosted_search(question):
    provider = Provider(web_result())
    location = Mock()
    location.get.return_value = None
    location.context.return_value = "Confirmed user location: saved city."
    answer = asyncio.run(agent(provider, location).ask(question, user_id=7))
    assert "Актуальный прогноз" in answer
    assert len(provider.requests) == 1
    assert provider.requests[0]["tools"] == [
        {"type": "web_search", "search_context_size": "medium"}
    ]
    assert provider.requests[0]["tool_choice"] == "required"


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LLMTimeoutError(), "OPENAI_TIMEOUT"),
        (LLMNetworkError(), "OPENAI_CONNECTION_ERROR"),
        (LLMRateLimitError(), "OPENAI_RATE_LIMIT"),
        (LLMQuotaError(), "OPENAI_QUOTA_EXCEEDED"),
        (LLMAuthenticationError(), "авторизации"),
        (LLMModelUnavailableError(), "недоступна"),
        (LLMBadRequestError(), "конфигурации"),
        (LLMProviderError(), "UNKNOWN_PROVIDER_ERROR"),
    ],
)
def test_safe_provider_error_mapping(error, code):
    answer = asyncio.run(agent(Provider(error)).ask("Привет"))
    assert code in answer
    assert "Traceback" not in answer


def base_env():
    return {
        "TELEGRAM_BOT_TOKEN": "test-token",
        "LLM_PROVIDER": "openai",
        "OPENAI_API_KEY": "test-key",
        "OPENAI_MODEL": "test-model",
        "TELEGRAM_ALLOWED_USER_IDS": "1",
    }


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("AGENT_REQUEST_DEADLINE_SECONDS", "0"),
        ("AGENT_REQUEST_DEADLINE_SECONDS", "4"),
        ("AGENT_REQUEST_DEADLINE_SECONDS", "301"),
        ("AGENT_REQUEST_DEADLINE_SECONDS", "bad"),
        ("OPENAI_REQUEST_TIMEOUT_SECONDS", "0"),
        ("OPENAI_REQUEST_TIMEOUT_SECONDS", "4"),
        ("OPENAI_REQUEST_TIMEOUT_SECONDS", "121"),
        ("OPENAI_REQUEST_TIMEOUT_SECONDS", "bad"),
        ("WEB_SEARCH_MAX_ATTEMPTS", "0"),
        ("WEB_SEARCH_MAX_ATTEMPTS", "4"),
        ("WEB_SEARCH_MAX_ATTEMPTS", "-1"),
        ("WEB_SEARCH_MAX_ATTEMPTS", "bad"),
        ("AGENT_MAX_TOOL_ITERATIONS", "0"),
        ("AGENT_MAX_TOOL_ITERATIONS", "11"),
        ("AGENT_MAX_TOOL_ITERATIONS", "-1"),
        ("AGENT_MAX_TOOL_ITERATIONS", "bad"),
    ],
)
def test_reliability_config_rejects_invalid_values(name, value):
    values = base_env()
    values[name] = value
    with pytest.raises(RuntimeError):
        load_config(values)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0123456789abcdef", "0123456789abcdef"),
        ("a" * 20, "a" * 20),
        ("f" * 32, "f" * 32),
        ("short", "none"),
        ("G" * 20, "none"),
        ("a" * 33, "none"),
    ],
)
def test_safe_correlation_id(value, expected):
    assert _safe_correlation_id(value) == expected
