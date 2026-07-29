"""Tests for Telegram command handlers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.ai.provider import (
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMTimeoutError,
)
from app.handlers import (
    HELP_MESSAGE,
    MAX_INPUT_LENGTH,
    START_MESSAGE,
    TELEGRAM_MESSAGE_LIMIT,
    authorize,
    handle_text,
    handle_unknown,
    help_command,
    log_incoming_update,
    ping,
    start,
    status,
    telegram_error_handler,
)


def make_update() -> SimpleNamespace:
    return SimpleNamespace(
        update_id=789,
        effective_message=SimpleNamespace(
            reply_text=AsyncMock(), text=None
        ),
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=456),
    )


def make_context(ai_client: Mock | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "ai_client": ai_client or Mock(),
                "agent": AsyncMock(),
                "user_locks": {},
                "config": SimpleNamespace(
                    allow_public_access=False,
                    telegram_allowed_user_ids=frozenset({123}),
                ),
            }
        ),
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
        error=None,
    )


def test_start_handler() -> None:
    update = make_update()

    asyncio.run(start(update, None))

    update.effective_message.reply_text.assert_awaited_once_with(START_MESSAGE)


def test_help_handler_lists_commands() -> None:
    update = make_update()

    asyncio.run(help_command(update, None))

    update.effective_message.reply_text.assert_awaited_once_with(HELP_MESSAGE)
    for command in ("/start", "/help", "/ping", "/status"):
        assert command in HELP_MESSAGE


def test_ping_handler() -> None:
    update = make_update()

    asyncio.run(ping(update, None))

    update.effective_message.reply_text.assert_awaited_once_with("Pong")


def test_status_handler_contains_system_information() -> None:
    update = make_update()

    asyncio.run(status(update, None))

    message = update.effective_message.reply_text.await_args.args[0]
    assert "Jarvis online" in message
    assert "Python version:" in message
    assert "Hostname:" in message
    assert "Current UTC time:" in message
    assert "Uptime:" in message


def test_text_handler_sends_ai_response() -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    context = make_context()
    context.application.bot_data["agent"].ask.return_value = "AI answer"

    asyncio.run(handle_text(update, context))

    context.application.bot_data["agent"].ask.assert_awaited_once_with(
        "Hello", user_id=123
    )
    update.effective_message.reply_text.assert_awaited_once_with("AI answer")


@pytest.mark.parametrize(
    "error",
    [
        LLMConfigurationError(),
        LLMTimeoutError(),
        LLMNetworkError(),
        LLMProviderError(),
        RuntimeError(),
    ],
)
def test_text_handler_hides_errors(
    error: Exception,
) -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    context = make_context()
    context.application.bot_data["agent"].ask.side_effect = error

    asyncio.run(handle_text(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(
        "Не удалось получить ответ от модели. Ошибка записана в журнал."
    )


def test_logs_safe_incoming_update_metadata(caplog: pytest.LogCaptureFixture) -> None:
    update = make_update()
    update.effective_message.text = "private message contents"

    with caplog.at_level("INFO", logger="app.handlers"):
        asyncio.run(log_incoming_update(update, None))

    assert "update_id=789 user_id=123 chat_id=456 message_type=text" in caplog.text
    assert "private message contents" not in caplog.text


def test_authorize_allows_allowlisted_user() -> None:
    update = make_update()
    context = make_context()
    context.application.bot_data["config"] = SimpleNamespace(
        allow_public_access=False,
        telegram_allowed_user_ids=frozenset({123}),
    )

    asyncio.run(authorize(update, context))

    update.effective_message.reply_text.assert_not_awaited()


def test_authorize_denies_unknown_user() -> None:
    from telegram.ext import ApplicationHandlerStop

    update = make_update()
    context = make_context()
    context.application.bot_data["config"] = SimpleNamespace(
        allow_public_access=False,
        telegram_allowed_user_ids=frozenset({999}),
    )

    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(authorize(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(
        "Доступ запрещён."
    )


def test_unknown_message_type_gets_clear_reply() -> None:
    update = make_update()
    update.effective_message.photo = [object()]

    asyncio.run(handle_unknown(update, None))

    update.effective_message.reply_text.assert_awaited_once_with(
        "Этот тип сообщения пока не поддерживается. Отправьте текст."
    )


def test_telegram_send_failure_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    update = make_update()
    context = make_context()
    context.error = RuntimeError("Telegram send failed")

    with caplog.at_level("ERROR", logger="app.handlers"):
        asyncio.run(telegram_error_handler(update, context))

    assert "update_id=789" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert "Telegram send failed" not in caplog.text


def test_authorize_allows_explicit_public_access() -> None:
    update = make_update()
    context = make_context()
    context.application.bot_data["config"] = SimpleNamespace(
        allow_public_access=True,
        telegram_allowed_user_ids=frozenset(),
    )

    asyncio.run(authorize(update, context))

    update.effective_message.reply_text.assert_not_awaited()


def test_text_handler_rejects_empty_message() -> None:
    update = make_update()
    update.effective_message.text = "   "
    context = make_context()

    asyncio.run(handle_text(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(
        "Сообщение пустое."
    )


def test_text_handler_rejects_long_message() -> None:
    update = make_update()
    update.effective_message.text = "x" * (MAX_INPUT_LENGTH + 1)
    context = make_context()

    asyncio.run(handle_text(update, context))

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "слишком длинное" in reply


def test_text_handler_splits_long_response() -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    response = "x" * (TELEGRAM_MESSAGE_LIMIT + 10)
    context = make_context()
    context.application.bot_data["agent"].ask.return_value = response

    asyncio.run(handle_text(update, context))

    assert update.effective_message.reply_text.await_count == 2
    assert (
        update.effective_message.reply_text.await_args_list[0].args[0]
        == response[:TELEGRAM_MESSAGE_LIMIT]
    )
    assert (
        update.effective_message.reply_text.await_args_list[1].args[0]
        == response[TELEGRAM_MESSAGE_LIMIT:]
    )


def test_text_handler_rejects_parallel_request() -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    context = make_context()
    context.application.bot_data["user_locks"][123] = SimpleNamespace(
        locked=lambda: True
    )

    asyncio.run(handle_text(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(
        "Предыдущий запрос ещё обрабатывается."
    )


@patch("app.handlers.asyncio.to_thread", new_callable=AsyncMock)
def test_run_tool_command_returns_json(to_thread: AsyncMock) -> None:
    from app.handlers import run_tool
    from app.tools.result import ToolResult

    update = make_update()
    manager = Mock()
    context = make_context()
    context.args = ["system_info"]
    context.application.bot_data["tool_manager"] = manager
    to_thread.return_value = ToolResult(
        success=True,
        tool="system_info",
        data={"hostname": "test-host"},
        message="Tool executed successfully.",
        duration_ms=1.0,
        error=None,
    )

    asyncio.run(run_tool(update, context))

    to_thread.assert_awaited_once_with(
        manager.execute, "system_info", initiator_user_id=123
    )
    reply = update.effective_message.reply_text.await_args.args[0]
    assert '"tool": "system_info"' in reply
    assert '"hostname": "test-host"' in reply


def test_run_tool_command_requires_name() -> None:
    from app.handlers import run_tool

    update = make_update()
    context = make_context()
    context.args = []

    asyncio.run(run_tool(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(
        "Использование:\n"
        "/tool system_info\n"
        "/tool remote_system_info <host>\n"
        "/tool remote_service_status <host> <service>"
    )


@patch("app.handlers.asyncio.to_thread", new_callable=AsyncMock)
def test_run_remote_service_tool_passes_only_validated_arguments(
    to_thread: AsyncMock,
) -> None:
    from app.handlers import run_tool
    from app.tools.result import ToolResult

    update = make_update()
    manager = Mock()
    context = make_context()
    context.args = ["remote_service_status", "crypto", "safe.service"]
    context.application.bot_data["tool_manager"] = manager
    to_thread.return_value = ToolResult(
        True, "remote_service_status", {}, "ok", 1, None
    )

    asyncio.run(run_tool(update, context))

    to_thread.assert_awaited_once_with(
        manager.execute,
        "remote_service_status",
        host_alias="crypto",
        service_name="safe.service",
        initiator_user_id=123,
    )


@patch("app.handlers.asyncio.to_thread", new_callable=AsyncMock)
def test_run_tool_is_never_available_via_public_access(
    to_thread: AsyncMock,
) -> None:
    from app.handlers import run_tool

    update = make_update()
    context = make_context()
    context.args = ["system_info"]
    context.application.bot_data["config"] = SimpleNamespace(
        allow_public_access=True,
        telegram_allowed_user_ids=frozenset(),
    )

    asyncio.run(run_tool(update, context))

    to_thread.assert_not_awaited()
    update.effective_message.reply_text.assert_awaited_once_with(
        "Доступ запрещён."
    )


def test_run_tool_rejects_arbitrary_command_text() -> None:
    from app.handlers import run_tool

    update = make_update()
    context = make_context()
    context.args = ["remote_system_info", "crypto", "uname", "-a"]

    asyncio.run(run_tool(update, context))

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "Использование:" in reply


def test_tools_command_lists_only_names_and_descriptions() -> None:
    from app.handlers import tools_command

    update = make_update()
    context = make_context()
    tool = SimpleNamespace(name="system_info", description="Safe diagnostics.")
    context.application.bot_data["tool_manager"] = SimpleNamespace(
        registry=SimpleNamespace(list_tools=lambda: [tool])
    )

    asyncio.run(tools_command(update, context))

    reply = update.effective_message.reply_text.await_args.args[0]
    assert "system_info" in reply
    assert "Safe diagnostics." in reply
    assert "command" not in reply.lower()
    assert "/etc/" not in reply
