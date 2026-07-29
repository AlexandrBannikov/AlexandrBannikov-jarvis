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
    help_command,
    ping,
    start,
    status,
)


def make_update() -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
        effective_user=SimpleNamespace(id=123),
        effective_chat=SimpleNamespace(id=456),
    )


def make_context(ai_client: Mock | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                "ai_client": ai_client or Mock(),
                "user_locks": {},
            }
        ),
        bot=SimpleNamespace(send_chat_action=AsyncMock()),
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


@patch("app.handlers.asyncio.to_thread", new_callable=AsyncMock)
def test_text_handler_sends_ai_response(to_thread: AsyncMock) -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    ai_client = Mock()
    to_thread.return_value = "AI answer"
    context = make_context(ai_client)

    asyncio.run(handle_text(update, context))

    to_thread.assert_awaited_once_with(ai_client.ask, "Hello")
    update.effective_message.reply_text.assert_awaited_once_with("AI answer")


@pytest.mark.parametrize(
    ("error", "expected_reply"),
    [
        (
            LLMConfigurationError(),
            "AI-сервис не настроен. Обратитесь к администратору.",
        ),
        (
            LLMTimeoutError(),
            "AI-сервис не ответил вовремя. Попробуйте ещё раз.",
        ),
        (
            LLMNetworkError(),
            "Не удалось подключиться к AI-сервису. Попробуйте позже.",
        ),
        (
            LLMProviderError(),
            "AI-сервис временно недоступен. Попробуйте позже.",
        ),
        (
            RuntimeError(),
            "Произошла внутренняя ошибка. Попробуйте позже.",
        ),
    ],
)
@patch("app.handlers.asyncio.to_thread", new_callable=AsyncMock)
def test_text_handler_hides_errors(
    to_thread: AsyncMock, error: Exception, expected_reply: str
) -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    ai_client = Mock()
    to_thread.side_effect = error
    context = make_context(ai_client)

    asyncio.run(handle_text(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(expected_reply)


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


@patch("app.handlers.asyncio.to_thread", new_callable=AsyncMock)
def test_text_handler_splits_long_response(to_thread: AsyncMock) -> None:
    update = make_update()
    update.effective_message.text = "Hello"
    response = "x" * (TELEGRAM_MESSAGE_LIMIT + 10)
    to_thread.return_value = response
    context = make_context()

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
