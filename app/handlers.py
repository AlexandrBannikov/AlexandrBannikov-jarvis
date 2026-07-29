"""Telegram command handlers."""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import logging
import platform
import socket
import time

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.ai.provider import (
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMTimeoutError,
)

logger = logging.getLogger(__name__)
START_MESSAGE = "Привет.\nЯ Jarvis.\nСистема запущена."
HELP_MESSAGE = (
    "Доступные команды:\n"
    "/start — запустить Jarvis\n"
    "/help — показать доступные команды\n"
    "/ping — проверить доступность\n"
    "/status — показать состояние системы"
)
PROCESS_STARTED_AT = time.monotonic()
MAX_INPUT_LENGTH = 4_000
TELEGRAM_MESSAGE_LIMIT = 4_096


async def authorize(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Stop processing updates from users outside the configured allowlist."""
    config = context.application.bot_data["config"]
    user = update.effective_user
    is_allowed = config.allow_public_access or (
        user is not None and user.id in config.telegram_allowed_user_ids
    )
    if is_allowed:
        return
    if update.effective_message:
        await update.effective_message.reply_text("Доступ запрещён.")
    raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond to /start."""
    del context
    if update.effective_message:
        await update.effective_message.reply_text(START_MESSAGE)


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Respond to /help."""
    del context
    if update.effective_message:
        await update.effective_message.reply_text(HELP_MESSAGE)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond to /ping."""
    del context
    if update.effective_message:
        await update.effective_message.reply_text("Pong")


def _format_uptime(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}d {hours:02d}:{minutes:02d}:{seconds:02d}"


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Respond to /status with process and host information."""
    del context
    now = datetime.now(timezone.utc)
    message = (
        "Jarvis online\n"
        f"Python version: {platform.python_version()}\n"
        f"Hostname: {socket.gethostname()}\n"
        f"Current UTC time: {now.isoformat(timespec='seconds')}\n"
        f"Uptime: {_format_uptime(time.monotonic() - PROCESS_STARTED_AT)}"
    )
    if update.effective_message:
        await update.effective_message.reply_text(message)


async def handle_text(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Send ordinary text messages to the configured LLM provider."""
    message = update.effective_message
    if message is None or message.text is None:
        return
    prompt = message.text.strip()
    if not prompt:
        await message.reply_text("Сообщение пустое.")
        return
    if len(prompt) > MAX_INPUT_LENGTH:
        await message.reply_text(
            f"Сообщение слишком длинное. Максимум: {MAX_INPUT_LENGTH} символов."
        )
        return

    ai_client = context.application.bot_data["ai_client"]
    user_id = update.effective_user.id if update.effective_user else 0
    locks = context.application.bot_data["user_locks"]
    user_lock = locks.setdefault(user_id, asyncio.Lock())
    if user_lock.locked():
        await message.reply_text("Предыдущий запрос ещё обрабатывается.")
        return

    async with user_lock:
        typing_task = asyncio.create_task(
            _show_typing(context, update.effective_chat.id)
        )
        try:
            response = await asyncio.to_thread(ai_client.ask, prompt)
        except LLMConfigurationError:
            response = "AI-сервис не настроен. Обратитесь к администратору."
        except LLMTimeoutError:
            response = "AI-сервис не ответил вовремя. Попробуйте ещё раз."
        except LLMNetworkError:
            response = "Не удалось подключиться к AI-сервису. Попробуйте позже."
        except LLMProviderError:
            response = "AI-сервис временно недоступен. Попробуйте позже."
        except Exception:
            logger.exception("Unexpected error while handling an AI request")
            response = "Произошла внутренняя ошибка. Попробуйте позже."
        finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task

        for chunk in _split_message(response):
            await message.reply_text(chunk)


async def _show_typing(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int
) -> None:
    """Refresh Telegram's typing indicator until cancelled."""
    while True:
        try:
            await context.bot.send_chat_action(
                chat_id=chat_id, action=ChatAction.TYPING
            )
        except Exception as error:
            logger.warning(
                "Could not send typing indicator: %s", type(error).__name__
            )
            return
        await asyncio.sleep(4)


def _split_message(text: str) -> list[str]:
    """Split a response into Telegram-safe chunks."""
    if not text:
        return ["AI-сервис вернул пустой ответ."]
    return [
        text[index : index + TELEGRAM_MESSAGE_LIMIT]
        for index in range(0, len(text), TELEGRAM_MESSAGE_LIMIT)
    ]
