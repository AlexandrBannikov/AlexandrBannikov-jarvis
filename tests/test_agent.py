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
from app.ai.prompts import PROCESS_UNAVAILABLE_MESSAGE
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
from app.ssh_agent.service_models import SSHServiceResult
from app.ssh_agent.tools import register_ssh_tools


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


class FakeSSHService:
    def __init__(self) -> None:
        self.top_calls: list[tuple[str, str, int]] = []
        self.summary_calls: list[str] = []

    async def get_top_processes(
        self, context, server_alias: str, sort_by: str, limit: int,
    ) -> SSHServiceResult:
        self.top_calls.append((server_alias, sort_by, limit))
        return SSHServiceResult(
            True, "top_processes", server_alias,
            data={
                "server_alias": server_alias,
                "sort_by": sort_by,
                "count": 1,
                "processes": ({
                    "pid": 1, "user": "root", "cpu_percent": 1.0,
                    "memory_percent": 2.0, "elapsed": "00:01",
                    "command": "python3",
                },),
            },
        )

    async def get_server_summary(
        self, context, server_alias: str,
    ) -> SSHServiceResult:
        self.summary_calls.append(server_alias)
        return SSHServiceResult(
            True, "server_summary", server_alias, data={"results": ()}
        )

    @staticmethod
    def format(result: SSHServiceResult) -> str:
        return "Безопасный результат."


def ssh_manager(service: FakeSSHService) -> ToolManager:
    registry = ToolRegistry()
    register_ssh_tools(registry, service)  # type: ignore[arg-type]
    return ToolManager(registry)


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


@pytest.mark.parametrize(
    ("question", "sort_by"),
    [
        ("Какие процессы больше всего грузят CPU?", "cpu"),
        ("Что использует больше всего памяти?", "memory"),
    ],
)
def test_process_questions_dispatch_top_process_tool(
    question: str, sort_by: str,
) -> None:
    service = FakeSSHService()
    provider = FakeProvider(
        [
            response(
                "r1",
                calls=[
                    call(
                        "c1",
                        "get_top_processes",
                        json.dumps({
                            "server_alias": "crypto",
                            "sort_by": sort_by,
                            "limit": 5,
                        }),
                    )
                ],
            ),
            response("r2", text="На сервере **crypto** нагрузка невысокая."),
        ]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider, ssh_manager(service), run_sync=run_immediately,
        ).ask(
            question, user_id=123, chat_id=456, source_message_id=7,
            is_allowlisted=True,
        )
    )

    assert service.top_calls == [("crypto", sort_by, 5)]
    assert answer.startswith("На сервере **crypto**")


def test_server_summary_request_does_not_dispatch_top_process_tool() -> None:
    service = FakeSSHService()
    provider = FakeProvider(
        [
            response(
                "r1",
                calls=[
                    call(
                        "c1", "get_server_summary",
                        '{"server_alias":"crypto"}',
                    )
                ],
            ),
            response("r2", text="Сводка сервера **crypto** готова."),
        ]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider, ssh_manager(service), run_sync=run_immediately,
        ).ask(
            "Покажи обычную сводку сервера crypto",
            user_id=123, chat_id=456, source_message_id=8,
            is_allowlisted=True,
        )
    )

    assert service.summary_calls == ["crypto"]
    assert not service.top_calls
    assert "**crypto**" in answer


def test_human_limit_message_avoids_internal_vocabulary_and_backticks() -> None:
    lowered = PROCESS_UNAVAILABLE_MESSAGE.lower()
    for forbidden in ("read-only инструмент", "schema", "alias", "tool"):
        assert forbidden not in lowered
    assert "`" not in PROCESS_UNAVAILABLE_MESSAGE


def test_web_search_is_not_offered_for_ordinary_question() -> None:
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
    assert not any(
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


def test_web_search_uses_retrieved_sources_when_citations_are_absent() -> None:
    provider = FakeProvider([
        SimpleNamespace(
            id="r1",
            output_text="Погода ясная.",
            output=[
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "sources": [
                            {"title": "Прогноз", "url": "https://example.com/weather"}
                        ]
                    },
                },
                {"type": "message", "content": [{"annotations": []}]},
            ],
        )
    ])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Какая сегодня погода в Москве?")
    )

    assert "Погода ясная." in answer
    assert "https://example.com/weather" in answer


def test_web_search_accepts_official_realtime_weather_feed() -> None:
    provider = FakeProvider([
        SimpleNamespace(
            id="r1",
            output_text="Погода ясная.",
            output=[
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "sources": [
                            {"type": "api", "name": "oai-weather", "url": None}
                        ]
                    },
                },
                {"type": "message", "content": [{"annotations": []}]},
            ],
        )
    ])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask("Какая сегодня погода в Москве?")
    )

    assert answer == "Погода ясная.\n\nИсточники:\n1. oai-weather"


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


def test_search_failure_does_not_fake_latest_version() -> None:
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
        ).ask("Какая последняя версия Python?")
    )

    assert answer == WEB_SEARCH_UNAVAILABLE_MESSAGE
    assert len(provider.requests) == 1


@pytest.mark.parametrize(
    "question",
    ["Привет", "Алло", "Как дела", "Спасибо", "2+2", "Что такое SSH?", "Расскажи про Москву"],
)
def test_ordinary_dialogue_never_receives_web_search(question: str) -> None:
    provider = FakeProvider([response("r1", text="Обычный ответ.")])

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
        ).ask(question)
    )

    assert answer == "Обычный ответ."
    assert all(tool["type"] != "web_search" for tool in provider.requests[0]["tools"])


def test_weather_without_location_requests_location() -> None:
    provider = FakeProvider([])
    location = Mock()
    location.context.return_value = None

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
            location_service=location,
        ).ask("Какая сегодня погода?", user_id=123)
    )

    assert answer == "Отправьте вашу геопозицию Telegram, чтобы я мог уточнить погоду."
    assert provider.requests == []


def test_weather_with_location_receives_web_search() -> None:
    provider = FakeProvider([
        web_response("r1", "Погода ясная.", [("Прогноз", "https://example.com/weather")])
    ])
    location = Mock()
    location.context.return_value = "Confirmed user location: Moscow."

    answer = asyncio.run(
        JarvisAgent(
            provider,
            manager(),
            run_sync=run_immediately,
            web_search_enabled=True,
            location_service=location,
        ).ask("Погода для моей геопозиции", user_id=123)
    )

    assert "Погода ясная." in answer
    assert any(tool["type"] == "web_search" for tool in provider.requests[0]["tools"])


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
            response("r1", calls=[call("c1"), call("c2", arguments='{\"kind\":2}')]),
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
    assert tool_manager.execute.call_count == 1


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
            response("r1", calls=[call("c1", arguments='{\"n\":1}')]),
            response("r2", calls=[call("c2", arguments='{\"n\":2}')]),
            response("r3", calls=[call("c3", arguments='{\"n\":3}')]),
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
        (LLMRateLimitError(), "OPENAI_RATE_LIMIT"),
        (LLMAuthenticationError(), "Ошибка авторизации"),
        (LLMNetworkError(), "OPENAI_CONNECTION_ERROR"),
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
