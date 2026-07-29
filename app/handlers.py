"""Telegram command handlers."""

import asyncio
from datetime import datetime, timezone
import logging
import platform
import socket
import time

from telegram import Update
from telegram.ext import ContextTypes

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
    if message is None or not message.text:
        return

    ai_client = context.application.bot_data["ai_client"]
    try:
        response = await asyncio.to_thread(ai_client.ask, message.text)
    except LLMConfigurationError:
        reply = "AI-сервис не настроен. Обратитесь к администратору."
    except LLMTimeoutError:
        reply = "AI-сервис не ответил вовремя. Попробуйте ещё раз."
    except LLMNetworkError:
        reply = "Не удалось подключиться к AI-сервису. Попробуйте позже."
    except LLMProviderError:
        reply = "AI-сервис временно недоступен. Попробуйте позже."
    except Exception:
        logger.exception("Unexpected error while handling an AI request")
        reply = "Произошла внутренняя ошибка. Попробуйте позже."
    else:
        reply = response

    await message.reply_text(reply)
