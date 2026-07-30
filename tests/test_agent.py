"""Network-free tests for the bounded OpenAI Responses agent loop."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.ai.agent import (
    EMPTY_RESPONSE_MESSAGE,
    TOOL_ROUND_LIMIT_MESSAGE,
    WEB_SEARCH_DISABLED_MESSAGE,
    WEB_SEARCH_EMPTY_MESSAGE,
    WEB_SEARCH_SECRET_MESSAGE,
    WEB_SEARCH_UNAVAILABLE_MESSAGE,
    WEB_SEARCH_UNSUPPORTED_MESSAGE,
    JarvisAgent,
)
from app.ai.provider import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMModelUnavailableError,
    LLMNetworkError,
    LLMPermissionError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMWebSearchUnavailableError,
    LLMWebSearchUnsupportedError,
)
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult
from app.tools.system_info import SystemInfoTool


async def run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


def response(
    response_id: str,
    *,
    text: str = "",
    calls: list[object] | None = None,
) -> object:
    return SimpleNamespace(
        id=response_id,
        output_text=text,
        output=calls or [],
    )


def call(call_id: str, name: str = "system_info", arguments: str = "{}"):
    return SimpleNamespace(
        type="function_call",
        call_id=call_id,
        name=name,
        arguments=arguments,
    )


def web_response(
    response_id: str,
    text: str,
    sources: list[tuple[str, str]],
    *,
    malformed: bool = False,
) -> object:
    annotations = [
        {
            "type": "url_citation",
            "url": "not-a-url" if malformed else url,
            "title": title,
        }
        for title, url in sources
    ]
    return SimpleNamespace(
        id=response_id,
        output_text=text,
        output=[
            {"type": "web_search_call", "status": "completed"},
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text,
                        "annotations": annotations,
                    }
                ],
            },
        ],
    )


class FakeProvider:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict] = []

    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def manager(result: ToolResult | None = None) -> ToolManager:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    tool_manager = ToolManager(registry)
    if result is not None:
        tool_manager.execute = Mock(return_value=result)  # type: ignore[method-assign]
    return tool_manager


def test_plain_text_response_needs_no_tool() -> None:
    provider = FakeProvider([response("r1", text="Обычный ответ")])

    answer = asyncio.run(
        JarvisAgent(provider, manager(), run_sync=run_immediately).ask(
            "Привет", 123
        )
    )

    assert answer == "Обычный ответ"
    assert len(provider.requests) == 1
    assert provider.requests[0]["tool_choice"] == "auto"
    assert provider.requests[0]["tools"]


def test_web_search_is_offered_but_not_required_for_ordinary_question() -> None:
    provider = FakeProvider([response("r1", text="systemd — менеджер служб.")])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Что такое systemd?")
    )

    assert answer == "systemd — менеджер служб."
    assert any(
        tool["type"] == "web_search"
        for tool in provider.requests[0]["tools"]
    )
    assert not any(
        item.get("type") == "web_search_call"
        for item in getattr(provider.responses, "output", [])
    )


@pytest.mark.parametrize(
    "question",
    [
        "Какая сейчас последняя стабильная версия Python?",
        "Найди в интернете последние новости OpenAI.",
    ],
)
def test_current_or_explicit_question_can_use_web_search(
    question: str,
) -> None:
    provider = FakeProvider(
        [
            web_response(
                "r1",
                "Актуальный ответ.",
                [("Python", "https://www.python.org/downloads/")],
            )
        ]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask(question)
    )

    assert "Актуальный ответ." in answer
    assert "Источники:" in answer
    assert "https://www.python.org/downloads/" in answer


def test_web_search_formats_multiple_unique_sources() -> None:
    response_with_sources = web_response(
        "r1",
        "Свежая информация.",
        [
            ("Источник A", "https://example.com/a"),
            ("Источник B", "https://example.org/b"),
            ("Дубликат", "https://example.com/a"),
        ],
    )

    answer = asyncio.run(
        JarvisAgent(
            FakeProvider([response_with_sources]),
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Что нового?")
    )

    assert answer.count("https://example.com/a") == 1
    assert "2. Источник B — https://example.org/b" in answer


def test_web_search_logs_only_safe_metrics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_query = "private-search-phrase"
    source_url = "https://example.com/result?private=value"
    provider = FakeProvider(
        [web_response("r1", "Актуально.", [("Source", source_url)])]
    )

    with caplog.at_level("INFO", logger="jarvis.audit"):
        asyncio.run(
            JarvisAgent(
                provider,
                manager(),
                run_sync=run_immediately,
                web_search_enabled=True,
            ).ask(private_query)
        )

    assert "web_search_used=true" in caplog.text
    assert "number_of_sources=1" in caplog.text
    assert private_query not in caplog.text
    assert source_url not in caplog.text
    assert "private=value" not in caplog.text


def test_explicit_web_search_reports_disabled_without_provider_call() -> None:
    provider = FakeProvider([])

    answer = asyncio.run(
        JarvisAgent(provider, manager(), run_sync=run_immediately).ask(
            "Найди в интернете свежие новости"
        )
    )

    assert answer == WEB_SEARCH_DISABLED_MESSAGE
    assert provider.requests == []


@pytest.mark.parametrize(
    "secret_request",
    [
        "Найди в интернете ключ " + "sk-" + "TEST_SECRET_VALUE",
        "Поищи Authorization: " + "Bear" + "er private-value",
        "Найди в интернете OPENAI_API_KEY=private-value",
    ],
)
def test_secret_request_is_never_given_web_search(
    secret_request: str,
) -> None:
    provider = FakeProvider([])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask(secret_request)
    )

    assert answer == WEB_SEARCH_SECRET_MESSAGE
    assert provider.requests == []


@pytest.mark.parametrize("malformed", [False, True])
def test_web_search_rejects_missing_or_malformed_citations(
    malformed: bool,
) -> None:
    sources = [("Broken", "https://example.com")] if malformed else []
    provider = FakeProvider(
        [web_response("r1", "Uncited current claim", sources, malformed=malformed)]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Что нового?")
    )

    assert answer == WEB_SEARCH_EMPTY_MESSAGE


def test_explicit_search_failure_has_specific_message() -> None:
    provider = FakeProvider([LLMWebSearchUnavailableError()])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Найди в интернете новости")
    )

    assert answer == WEB_SEARCH_UNAVAILABLE_MESSAGE


def test_unsupported_search_model_has_specific_message() -> None:
    provider = FakeProvider([LLMWebSearchUnsupportedError()])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Что нового?")
    )

    assert answer == WEB_SEARCH_UNSUPPORTED_MESSAGE


def test_bad_request_does_not_claim_web_search_is_unsupported() -> None:
    answer = asyncio.run(
        JarvisAgent(
            FakeProvider([LLMBadRequestError()]),
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Покажи список серверов")
    )

    assert answer == (
        "Не удалось обработать запрос из-за ошибки конфигурации инструментов."
    )
    assert answer != WEB_SEARCH_UNSUPPORTED_MESSAGE


def test_search_failure_can_fall_back_for_stable_question() -> None:
    provider = FakeProvider(
        [
            LLMWebSearchUnavailableError(),
            response("r2", text="Стабильный ответ без поиска."),
        ]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Объясни арифметику")
    )

    assert answer == "Стабильный ответ без поиска."
    assert all(
        tool["type"] != "web_search"
        for tool in provider.requests[1]["tools"]
    )


def test_one_tool_call_uses_matching_call_id_and_previous_response() -> None:
    provider = FakeProvider(
        [
            response("r1", calls=[call("call-1")]),
            response("r2", text="Система работает."),
        ]
    )
    result = ToolResult(
        True, "system_info", {"hostname": "jarvis"}, "ok", 1
    )
    tool_manager = manager(result)

    answer = asyncio.run(
        JarvisAgent(
            provider, tool_manager, run_sync=run_immediately
        ).ask("Проверь сервер", 123)
    )

    assert answer == "Система работает."
    tool_manager.execute.assert_called_once_with("system_info")
    second = provider.requests[1]
    assert second["previous_response_id"] == "r1"
    assert second["input_items"][0]["type"] == "function_call_output"
    assert second["input_items"][0]["call_id"] == "call-1"
    output = json.loads(second["input_items"][0]["output"])
    assert output["success"] is True


def test_multiple_tool_calls_are_returned_together() -> None:
    provider = FakeProvider(
        [
            response("r1", calls=[call("c1"), call("c2")]),
            response("r2", text="Обе проверки завершены."),
        ]
    )
    tool_manager = manager(
        ToolResult(True, "system_info", {"ok": True}, "ok", 1)
    )

    asyncio.run(
        JarvisAgent(
            provider, tool_manager, run_sync=run_immediately
        ).ask("Проверь всё")
    )

    outputs = provider.requests[1]["input_items"]
    assert [item["call_id"] for item in outputs] == ["c1", "c2"]
    assert tool_manager.execute.call_count == 2


@pytest.mark.parametrize(
    ("name", "arguments", "expected_error"),
    [
        ("missing", "{}", "unknown_tool"),
        ("system_info", "{bad", "invalid_tool_arguments"),
        ("system_info", '{"extra":1}', "invalid_tool_arguments"),
    ],
)
def test_invalid_call_is_not_executed_and_error_is_returned_to_model(
    name: str, arguments: str, expected_error: str
) -> None:
    provider = FakeProvider(
        [
            response("r1", calls=[call("c1", name, arguments)]),
            response("r2", text="Вызов отклонён."),
        ]
    )
    tool_manager = manager()
    tool_manager.execute = Mock()  # type: ignore[method-assign]

    answer = asyncio.run(
        JarvisAgent(
            provider, tool_manager, run_sync=run_immediately
        ).ask("test")
    )

    assert answer == "Вызов отклонён."
    tool_manager.execute.assert_not_called()
    output = json.loads(provider.requests[1]["input_items"][0]["output"])
    assert output["success"] is False
    assert output["error"] == expected_error


def test_unsuccessful_tool_result_is_returned_to_model() -> None:
    provider = FakeProvider(
        [
            response("r1", calls=[call("c1")]),
            response("r2", text="Проверка завершилась ошибкой."),
        ]
    )
    failed = ToolResult(
        False, "system_info", {}, "Tool execution failed.", 1, "command_failed"
    )

    asyncio.run(
        JarvisAgent(
            provider, manager(failed), run_sync=run_immediately
        ).ask("test")
    )

    output = json.loads(provider.requests[1]["input_items"][0]["output"])
    assert output["success"] is False
    assert output["error"] == "command_failed"


def test_tool_round_limit_stops_loop() -> None:
    provider = FakeProvider(
        [
            response("r1", calls=[call("c1")]),
            response("r2", calls=[call("c2")]),
            response("r3", calls=[call("c3")]),
        ]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            max_tool_rounds=2,
            run_sync=run_immediately,
        ).ask("loop")
    )

    assert answer == TOOL_ROUND_LIMIT_MESSAGE
    assert len(provider.requests) == 3


def test_empty_final_response_has_safe_message() -> None:
    answer = asyncio.run(
        JarvisAgent(
            FakeProvider([response("r1")]),
            manager(),
            run_sync=run_immediately,
        ).ask("test")
    )

    assert answer == EMPTY_RESPONSE_MESSAGE


@pytest.mark.parametrize(
    ("error", "message_part"),
    [
        (LLMTimeoutError(), "Временная ошибка OpenAI"),
        (LLMRateLimitError(), "Превышен лимит OpenAI"),
        (LLMAuthenticationError(), "Ошибка авторизации"),
        (LLMNetworkError(), "Временная ошибка OpenAI"),
        (LLMPermissionError(), "недоступна для этого проекта"),
        (LLMBadRequestError(), "ошибки конфигурации инструментов"),
        (LLMModelUnavailableError(), "модель недоступна"),
    ],
)
def test_provider_errors_are_safe(error: Exception, message_part: str) -> None:
    answer = asyncio.run(
        JarvisAgent(
            FakeProvider([error]),
            manager(),
            run_sync=run_immediately,
        ).ask("test")
    )

    assert message_part in answer
