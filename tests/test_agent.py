"""Network-free tests for the bounded OpenAI Responses agent loop."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.ai.agent import (
    EMPTY_RESPONSE_MESSAGE,
    TOOL_ROUND_LIMIT_MESSAGE,
    JarvisAgent,
)
from app.ai.provider import (
    LLMAuthenticationError,
    LLMNetworkError,
    LLMRateLimitError,
    LLMTimeoutError,
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
        (LLMTimeoutError(), "не ответил вовремя"),
        (LLMRateLimitError(), "ограничил запросы"),
        (LLMAuthenticationError(), "учётные данные"),
        (LLMNetworkError(), "подключиться"),
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
