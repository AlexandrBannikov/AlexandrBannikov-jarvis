"""Tests for the optional Telegram startup notification."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.bot import send_startup_notification


def application(enabled: bool, users: frozenset[int]) -> SimpleNamespace:
    return SimpleNamespace(
        bot_data={
            "config": SimpleNamespace(
                telegram_startup_notification=enabled,
                telegram_allowed_user_ids=users,
            )
        },
        bot=SimpleNamespace(send_message=AsyncMock()),
    )


def test_startup_notification_targets_first_allowed_user() -> None:
    app = application(True, frozenset({456, 123}))

    asyncio.run(send_startup_notification(app))

    app.bot.send_message.assert_awaited_once_with(
        chat_id=123, text="Jarvis запущен и готов к работе."
    )


def test_startup_notification_disabled() -> None:
    app = application(False, frozenset({123}))

    asyncio.run(send_startup_notification(app))

    app.bot.send_message.assert_not_awaited()


def test_startup_notification_error_is_nonfatal() -> None:
    app = application(True, frozenset({123}))
    app.bot.send_message.side_effect = RuntimeError("failure")

    asyncio.run(send_startup_notification(app))
