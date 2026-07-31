"""Telegram command handlers."""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone
import json
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
from app.reminders.service import ReminderError

logger = logging.getLogger(__name__)
START_MESSAGE = "Привет.\nЯ Jarvis.\nСистема запущена."
HELP_MESSAGE = (
    "Доступные команды:\n"
    "/start — запустить Jarvis\n"
    "/help — показать доступные команды\n"
    "/ping — проверить доступность\n"
    "/health — проверить готовность Jarvis\n"
    "/status — показать состояние системы\n"
    "/tool system_info — локальная диагностика\n"
    "Проверки удалённых серверов выполняются только через утверждённые SSH tools.\n"
    "/tools — показать безопасные инструменты"
    "\n/skills — показать состояние встроенных навыков"
    "\n/memory — краткая сводка долговременной памяти"
    "\n/memory_projects — известные проекты"
    "\n/memory_forget <id> — забыть принадлежащую вам запись"
    "\n/conversation — показать текущую тему"
    "\n/reset_context — начать текущий диалог заново"
)
PROCESS_STARTED_AT = time.monotonic()
MAX_INPUT_LENGTH = 4_000
TELEGRAM_MESSAGE_LIMIT = 4_096


def _message_type(update: Update) -> str:
    """Return a safe message classification without inspecting its contents."""
    message = update.effective_message
    if message is None:
        return "no_message"
    if getattr(message, "text", None) is not None:
        return "command" if message.text.startswith("/") else "text"
    for kind in (
        "photo",
        "video",
        "audio",
        "voice",
        "document",
        "sticker",
        "location",
        "contact",
    ):
        if getattr(message, kind, None):
            return kind
    return "other"


async def log_incoming_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log only routing metadata for an incoming Telegram update."""
    del context
    user = update.effective_user
    chat = update.effective_chat
    logger.info(
        "Telegram update received: update_id=%s user_id=%s chat_id=%s "
        "message_type=%s",
        update.update_id,
        user.id if user else None,
        chat.id if chat else None,
        _message_type(update),
    )


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
        logger.info(
            "Telegram update authorized: update_id=%s user_id=%s",
            update.update_id,
            user.id if user else None,
        )
        return
    logger.warning(
        "Telegram update blocked by allowlist: update_id=%s user_id=%s",
        update.update_id,
        user.id if user else None,
    )
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


async def health_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Report application readiness without exposing configuration."""
    del context
    if update.effective_message:
        await update.effective_message.reply_text("Jarvis healthy")


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


async def run_tool(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Temporarily expose registered tools for direct verification."""
    message = update.effective_message
    if message is None:
        return
    user = update.effective_user
    config = context.application.bot_data["config"]
    if (
        user is None
        or user.id not in config.telegram_allowed_user_ids
    ):
        await message.reply_text("Доступ запрещён.")
        return

    arguments = list(context.args)
    parameters: dict[str, object]
    if arguments == ["system_info"]:
        tool_name = "system_info"
        parameters = {}
    else:
        await message.reply_text(
            "Использование:\n/tool system_info\n"
            "Удалённые проверки запрашивайте обычным сообщением."
        )
        return

    parameters["initiator_user_id"] = user.id
    manager = context.application.bot_data["tool_manager"]
    result = await asyncio.to_thread(
        manager.execute, tool_name, **parameters
    )
    response = json.dumps(asdict(result), ensure_ascii=False, indent=2)
    for chunk in _split_message(response):
        await message.reply_text(chunk)


async def tools_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """List only public names and descriptions of registered tools."""
    message = update.effective_message
    if message is None:
        return
    manager = context.application.bot_data["tool_manager"]
    lines = ["Доступные read-only инструменты:"]
    lines.extend(
        f"- {tool.name}: {tool.description}"
        for tool in manager.registry.list_tools()
    )
    await message.reply_text("\n".join(lines))


async def skills_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Show compact, secret-free health of built-in capabilities."""
    message = update.effective_message
    registry = context.application.bot_data.get("skill_registry")
    if message is None:
        return
    if registry is None:
        await message.reply_text("🧩 Навыки Jarvis\nРеестр недоступен.")
        return
    lines = ["🧩 Навыки Jarvis"]
    for report in registry.health():
        icon = {
            "ok": "✅", "warning": "⚠️", "error": "❌", "disabled": "⏸️",
        }[report.health_status.value]
        lines.append(f"{icon} {report.metadata.name}")
        lines.append(f"   Инструментов: {len(report.metadata.tool_names)}")
        if report.health_status.value != "ok":
            lines.append(f"   Статус: {report.health_message}")
    response = "\n".join(lines)
    for chunk in _split_message(response):
        await message.reply_text(chunk)

async def conversation_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    manager = context.application.bot_data.get("conversation_manager")
    if message is None or manager is None:
        if message: await message.reply_text("Текущее состояние диалога недоступно.")
        return
    key = manager.key(_memory_owner(update), update.effective_chat.id if update.effective_chat else 0)
    await message.reply_text(manager.summary(key))

async def reset_context_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    manager = context.application.bot_data.get("conversation_manager")
    if message is None or manager is None: return
    manager.storage.clear(manager.key(_memory_owner(update), update.effective_chat.id if update.effective_chat else 0))
    await message.reply_text("Текущая тема диалога сброшена.")


def _memory_owner(update: Update) -> int:
    return update.effective_user.id if update.effective_user else 0


async def memory_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message=update.effective_message
    if message is None: return
    manager=context.application.bot_data.get("memory_manager")
    if manager is None:
        await message.reply_text("Долговременная память отключена.")
        return
    owner=_memory_owner(update)
    records=manager.service.recall(owner,limit=8)
    projects=manager.storage.list_projects(owner)
    lines=[f"Память включена. Активных записей: {manager.storage.count_active(owner)}.",
           "Известные проекты: "+(", ".join(p.name for p in projects) or "нет.")]
    lines.extend(f"- #{r.id} [{r.scope}] {r.summary}" for r in records[:5])
    await message.reply_text("\n".join(lines))


async def memory_status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await memory_command(update,context)


async def memory_projects_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message=update.effective_message
    if message is None: return
    manager=context.application.bot_data.get("memory_manager")
    if manager is None:
        await message.reply_text("Долговременная память отключена."); return
    projects=manager.storage.list_projects(_memory_owner(update))
    await message.reply_text("\n".join(
        [f"- {p.name}: {p.status or p.current_milestone or 'статус не указан'}"
         for p in projects]) or "Известных проектов нет.")


async def memory_forget_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message=update.effective_message
    if message is None: return
    manager=context.application.bot_data.get("memory_manager")
    if manager is None:
        await message.reply_text("Долговременная память отключена."); return
    if len(context.args)!=1 or not context.args[0].isdigit():
        await message.reply_text("Использование: /memory_forget <id>"); return
    forgotten=manager.service.forget(_memory_owner(update),int(context.args[0]))
    await message.reply_text("Запись забыта." if forgotten else
                             "Запись не найдена или принадлежит другому пользователю.")


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

    agent = context.application.bot_data["agent"]
    user_id = update.effective_user.id if update.effective_user else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    reminder_service = context.application.bot_data.get("reminder_service")
    locks = context.application.bot_data["user_locks"]
    user_lock = locks.setdefault(user_id, asyncio.Lock())
    if user_lock.locked():
        await message.reply_text("Предыдущий запрос ещё обрабатывается.")
        return

    async with user_lock:
        if reminder_service is not None:
            try:
                reminder_response = await asyncio.to_thread(
                    reminder_service.parse_and_handle,
                    prompt,
                    user_id=user_id,
                    chat_id=chat_id,
                    source_message_id=getattr(message, "message_id", None),
                )
            except ReminderError as error:
                logger.info(
                    "Reminder request rejected: user_id=%s error_code=%s",
                    user_id,
                    error.code,
                )
                reminder_response = error.user_message
            if reminder_response is not None:
                for chunk in _split_message(reminder_response):
                    await message.reply_text(chunk)
                return
        typing_task = asyncio.create_task(
            _show_typing(context, update.effective_chat.id)
        )
        try:
            ask_kwargs = dict(user_id=user_id, chat_id=chat_id,
                              source_message_id=getattr(message, "message_id", None),
                              is_allowlisted=(user_id in context.application.bot_data["config"].telegram_allowed_user_ids))
            thread_id = getattr(message, "message_thread_id", None)
            reply_to = getattr(getattr(message, "reply_to_message", None), "message_id", None)
            if thread_id is not None: ask_kwargs["thread_id"] = thread_id
            if reply_to is not None: ask_kwargs["reply_to_message_id"] = reply_to
            response = await agent.ask(prompt, **ask_kwargs)
        except (
            LLMConfigurationError,
            LLMTimeoutError,
            LLMNetworkError,
            LLMProviderError,
        ) as error:
            logger.error(
                "LLM request failed: error_type=%s", type(error).__name__
            )
            response = (
                "Не удалось получить ответ от модели. "
                "Ошибка записана в журнал."
            )
        except Exception as error:
            logger.exception(
                "Unexpected LLM request failure: error_type=%s",
                type(error).__name__,
            )
            response = (
                "Не удалось получить ответ от модели. "
                "Ошибка записана в журнал."
            )
        finally:
            typing_task.cancel()
            with suppress(asyncio.CancelledError):
                await typing_task

        for chunk in _split_message(response):
            await message.reply_text(chunk)


async def handle_unknown(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Give unsupported message types a clear local response."""
    del context
    if update.effective_message:
        await update.effective_message.reply_text(
            "Этот тип сообщения пока не поддерживается. Отправьте текст."
        )


async def telegram_error_handler(
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Log Telegram handler/send failures without message or secret contents."""
    update_id = getattr(update, "update_id", None)
    error = context.error
    logger.error(
        "Telegram update processing failed: update_id=%s error_type=%s",
        update_id,
        type(error).__name__,
    )


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
