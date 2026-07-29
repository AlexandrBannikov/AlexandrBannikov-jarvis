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
    START_MESSAGE,
    handle_text,
    help_command,
    ping,
    start,
    status,
)


def make_update() -> SimpleNamespace:
    return SimpleNamespace(
        effective_message=SimpleNamespace(reply_text=AsyncMock())
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
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"ai_client": ai_client})
    )

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
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={"ai_client": ai_client})
    )

    asyncio.run(handle_text(update, context))

    update.effective_message.reply_text.assert_awaited_once_with(expected_reply)
