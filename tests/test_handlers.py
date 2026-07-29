"""Tests for Telegram command handlers."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.handlers import HELP_MESSAGE, START_MESSAGE, help_command, ping, start, status


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
