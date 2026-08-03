"""Telegram command handlers."""

import asyncio
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone
import json
import logging
import platform
import socket
import secrets
import time
import mimetypes
from pathlib import Path
import re
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction
from telegram.ext import ApplicationHandlerStop, ContextTypes

from app.ai.provider import (
    LLMConfigurationError,
    LLMNetworkError,
    LLMProviderError,
    LLMTimeoutError,
)
from app.reminders.service import ReminderError
from app.location.models import LocationCandidate
from zoneinfo import ZoneInfo
from app.access import CapabilityPolicy, Principal, OWNER
from app.documents.service import DocumentError
from app.documents.validators import ValidationError
from app.routing import RequestIntent, UniversalRequestRouter

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
    "\n/location — показать подтверждённое местоположение"
    "\n/timezone — показать часовой пояс и местное время"
    "\n/clear_location — удалить местоположение"
    "\n/invite_family — создать одноразовое семейное приглашение"
    "\n/family_users — семейные пользователи"
    "\n/revoke_family_invite — отозвать ожидающие приглашения"
    "\n/disable_family_user <id> — временно отключить доступ"
    "\n/enable_family_user <id> — включить доступ"
    "\n/remove_family_user <id> — отозвать доступ без удаления данных"
)
FAMILY_HELP_MESSAGE = (
    "Доступные возможности:\n"
    "обычные вопросы, интернет-поиск, погода, личная и семейная память, "
    "напоминания, геопозиция и часовой пояс.\n"
    "/help /ping /skills /tools /memory /conversation /location /timezone"
)
INVITE_ONLY_MESSAGE = (
    "Доступ к этому боту предоставляется только по приглашению владельца."
)
PROCESS_STARTED_AT = time.monotonic()
MAX_INPUT_LENGTH = 4_000
TELEGRAM_MESSAGE_LIMIT = 4_096


_DOCUMENT_REFERENCE = re.compile(
    r"(?i)\b(?:документ|файл|таблиц|лист|страниц|текст[ае]?|автор|раздел|"
    r"тезис|конспект|в\s+н[её]м|из\s+него|эт(?:от|ом)\s+(?:файл|документ|текст))\w*"
)


def _should_attach_document_context(route: object, text: str = "") -> bool:
    """Keep an active document from hijacking an unrelated routed request."""
    capabilities = set(getattr(route, "required_capabilities", ()) or ())
    private_or_current = {
        "web_search", "crypto_control", "ssh", "reminders", "memory", "location",
    }
    return not bool(capabilities & private_or_current) and bool(
        _DOCUMENT_REFERENCE.search(text)
    )


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


def _principal(update: Update, context) -> Principal | None:
    user = getattr(update, "effective_user", None)
    if user is None:
        return None
    storage = context.application.bot_data.get("access_storage")
    if storage is not None:
        return storage.principal(user.id)
    config = context.application.bot_data["config"]
    if user.id in config.telegram_allowed_user_ids:
        return Principal(user.id, OWNER, "active")
    return None


async def authorize(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Stop processing updates from users outside the configured allowlist."""
    user = update.effective_user
    principal = _principal(update, context)
    if principal is not None and principal.status == "active":
        if hasattr(context, "user_data"):
            context.user_data["principal"] = principal
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
    text = str(getattr(update.effective_message, "text", "") or "")
    if text.startswith("/start "):
        return
    if update.effective_message:
        await update.effective_message.reply_text(INVITE_ONLY_MESSAGE)
    raise ApplicationHandlerStop


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE | None) -> None:
    """Respond to /start."""
    message = update.effective_message
    if message is None:
        return
    if context is None or not getattr(context, "args", None):
        await message.reply_text(START_MESSAGE)
        return
    user = update.effective_user
    storage = context.application.bot_data.get("access_storage")
    if user is None or storage is None:
        await message.reply_text(INVITE_ONLY_MESSAGE)
        return
    result = storage.redeem(
        context.args[0], user.id,
        getattr(user, "full_name", "") or "",
        getattr(user, "username", "") or "",
    )
    if result == "created":
        await message.reply_text("Доступ активирован. Добро пожаловать в Jarvis!")
    else:
        await message.reply_text("Приглашение недействительно, использовано или истекло.")


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Respond to /help."""
    if update.effective_message:
        principal = _principal(update, context) if context is not None else None
        await update.effective_message.reply_text(
            FAMILY_HELP_MESSAGE if principal and principal.role == "family_user" else HELP_MESSAGE
        )


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
    if context is not None:
        principal = _principal(update, context)
        policy = context.application.bot_data.get("capability_policy", CapabilityPolicy())
        if not policy.require(principal, "technical.production_diagnostics"):
            if update.effective_message:
                await update.effective_message.reply_text(
                    "Эта техническая функция доступна только владельцу."
                )
            return
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
    principal = _principal(update, context)
    policy = context.application.bot_data.get("capability_policy", CapabilityPolicy())
    if user is None or not policy.require(principal, "technical.production_diagnostics"):
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
    principal = _principal(update, context)
    policy = context.application.bot_data.get("capability_policy", CapabilityPolicy())
    lines.extend(
        f"- {tool.name}: {tool.description}"
        for tool in manager.registry.list_tools()
        if policy.allows(principal, policy.tool_capability(tool.name))
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
    principal = _principal(update, context)
    policy = context.application.bot_data.get("capability_policy", CapabilityPolicy())
    for report in registry.health():
        if report.metadata.skill_id == "ssh" and not policy.allows(principal, "technical.ssh"):
            continue
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


async def invite_family_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    principal = _principal(update, context)
    policy = context.application.bot_data["capability_policy"]
    if message is None or not policy.require(principal, "admin.invites"):
        if message: await message.reply_text("Доступ запрещён.")
        return
    token = context.application.bot_data["access_storage"].create_invite(
        principal.user_id, context.application.bot_data["config"].family_invite_ttl_seconds
    )
    bot = await context.bot.get_me()
    await message.reply_text(
        f"Одноразовая ссылка действует ограниченное время:\nhttps://t.me/{bot.username}?start={token}"
    )


async def family_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    principal = _principal(update, context)
    if message is None or not context.application.bot_data["capability_policy"].require(principal, "admin.users"):
        if message: await message.reply_text("Доступ запрещён.")
        return
    users = context.application.bot_data["access_storage"].list_family()
    lines = ["Семейные пользователи:"]
    for user in users:
        name = user.display_name or "Без имени"
        username = f" (@{user.username})" if user.username else ""
        lines.append(f"- {name}{username}: {user.role}, {user.status}")
    await message.reply_text("\n".join(lines) if users else "Семейных пользователей нет.")


async def disable_family_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    principal = _principal(update, context)
    if message is None or not context.application.bot_data["capability_policy"].require(principal, "admin.users"):
        if message: await message.reply_text("Доступ запрещён.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await message.reply_text("Использование: /disable_family_user <Telegram ID>")
        return
    changed = context.application.bot_data["access_storage"].set_family_status(
        int(context.args[0]), "disabled"
    )
    await message.reply_text("Доступ отключён, данные сохранены." if changed else "Пользователь не найден.")


async def enable_family_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_family_user_command(update, context, "active", "Доступ включён.")


async def remove_family_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_family_user_command(
        update, context, "removed", "Доступ отозван, данные не удалены."
    )


async def _set_family_user_command(update, context, status_value: str, success: str) -> None:
    message = update.effective_message
    principal = _principal(update, context)
    if message is None or not context.application.bot_data["capability_policy"].require(principal, "admin.users"):
        if message: await message.reply_text("Доступ запрещён.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await message.reply_text("Укажите Telegram ID семейного пользователя.")
        return
    changed = context.application.bot_data["access_storage"].set_family_status(
        int(context.args[0]), status_value
    )
    await message.reply_text(success if changed else "Пользователь не найден.")


async def revoke_family_invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    principal = _principal(update, context)
    if message is None or not context.application.bot_data["capability_policy"].require(principal, "admin.invites"):
        if message: await message.reply_text("Доступ запрещён.")
        return
    count = context.application.bot_data["access_storage"].revoke_pending_invites(principal.user_id)
    await message.reply_text(f"Отозвано ожидающих приглашений: {count}.")

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


def _utc_offset(name: str) -> str:
    offset=datetime.now(ZoneInfo(name)).utcoffset(); seconds=int(offset.total_seconds()) if offset else 0
    sign="+" if seconds>=0 else "-"; hours,remainder=divmod(abs(seconds),3600)
    return f"{sign}{hours:02d}:{remainder//60:02d}"

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message,user=update.effective_message,update.effective_user
    service=context.application.bot_data.get("location_service")
    if message is None or user is None or getattr(message,"location",None) is None:return
    if service is None: await message.reply_text("Поддержка местоположения отключена."); return
    try: item=await asyncio.to_thread(service.resolve,message.location.latitude,message.location.longitude)
    except (ValueError,LookupError): await message.reply_text("Не удалось определить часовой пояс для этих координат."); return
    except Exception:
        logger.exception("Location resolution failed")
        await message.reply_text("Сервис определения местоположения временно недоступен."); return
    nonce=secrets.token_urlsafe(8)
    context.application.bot_data["pending_locations"][user.id]=(nonce,item)
    city=item.city or "не удалось определить"; country=f"\nСтрана:\n{item.country}" if item.country else ""
    text=(f"Получил ваше местоположение.\n\nГород:\n{city}{country}\n\nЧасовой пояс:\n{item.timezone}\n\nUTC:\n{_utc_offset(item.timezone)}\n\nСохранить это местоположение?")
    keyboard=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Сохранить",callback_data=f"location:save:{nonce}"),InlineKeyboardButton("❌ Не сохранять",callback_data=f"location:discard:{nonce}")]])
    await message.reply_text(text,reply_markup=keyboard)

async def location_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query,user=update.callback_query,update.effective_user
    if query is None or user is None:return
    await query.answer(); parts=(query.data or "").split(":",2); pending=context.application.bot_data["pending_locations"].get(user.id)
    if len(parts)!=3 or pending is None or parts[2]!=pending[0]:await query.edit_message_text("Запрос устарел. Отправьте геопозицию ещё раз.");return
    action, item=parts[1],pending[1];context.application.bot_data["pending_locations"].pop(user.id,None)
    if action=="discard":await query.edit_message_text("Местоположение не сохранено.");return
    if action!="save":await query.edit_message_text("Запрос устарел. Отправьте геопозицию ещё раз.");return
    await asyncio.to_thread(context.application.bot_data["location_service"].save,user.id,item)
    await query.edit_message_text("Местоположение сохранено.")

async def location_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message,user=update.effective_message,update.effective_user; service=context.application.bot_data.get("location_service")
    if message is None or user is None:return
    item=service.get(user.id) if service else None
    if not item:await message.reply_text("Сохранённого местоположения нет. Отправьте геопозицию Telegram.");return
    await message.reply_text(f"Ваше местоположение:\n\nГород:\n{item.city or 'не определён'}\n\nTimezone:\n{item.timezone}\n\nUTC:\n{_utc_offset(item.timezone)}\n\nОбновлено:\n{item.updated_at} UTC")

async def timezone_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message,user=update.effective_message,update.effective_user; service=context.application.bot_data.get("location_service")
    if message is None or user is None:return
    item=service.get(user.id) if service else None
    if not item:await message.reply_text("Часовой пояс не настроен. Отправьте геопозицию Telegram.");return
    await message.reply_text(f"Ваш часовой пояс:\n\n{item.timezone}\n\nМестное время:\n{datetime.now(ZoneInfo(item.timezone)).strftime('%H:%M')}")

async def clear_location_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message,user=update.effective_message,update.effective_user; service=context.application.bot_data.get("location_service")
    if message is None or user is None:return
    context.application.bot_data["pending_locations"].pop(user.id,None)
    removed=service.clear(user.id) if service else False
    await message.reply_text("Местоположение удалено." if removed else "Сохранённого местоположения нет.")

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
    correlation_id = uuid.uuid4().hex[:20]
    request_deadline = float(context.application.bot_data.get(
        "request_deadline_seconds", 45
    ))
    deadline_at = asyncio.get_running_loop().time() + request_deadline
    processed_updates = context.application.bot_data.setdefault("processed_updates", set())
    update_id = getattr(update, "update_id", None)
    if update_id is not None and update_id in processed_updates:
        logger.info("duplicate_update correlation_id=%s status=ignored", correlation_id)
        return
    if update_id is not None:
        processed_updates.add(update_id)
        if len(processed_updates) > 2048:
            processed_updates.pop()
    locks = context.application.bot_data["user_locks"]
    scope = (user_id, chat_id)
    user_lock = locks.setdefault(scope, asyncio.Lock())
    if user_lock.locked() and re.search(r"(?i)^\s*(?:ты\s+)?завис\??\s*$", prompt):
        await message.reply_text("Предыдущий запрос ещё обрабатывается.")
        logger.info("request_status correlation_id=%s state=active", correlation_id)
        return

    async with user_lock:
        principal = _principal(update, context)
        policy = context.application.bot_data.get("capability_policy", CapabilityPolicy())
        if not policy.require(principal, "assistant.chat"):
            await message.reply_text(INVITE_ONLY_MESSAGE)
            return
        limiter = context.application.bot_data.get("rate_limiter")
        if limiter is not None and not limiter.message(principal):
            await message.reply_text("Слишком много запросов. Попробуйте через минуту.")
            return
        location_service = context.application.bot_data.get("location_service")
        location_available = bool(
            location_service is not None and location_service.get(user_id)
        )
        preliminary_route = UniversalRequestRouter().classify(
            prompt, location_available=location_available
        )
        # Keep the fast deterministic reminder path for a single action. A
        # compound request must reach the agent so every independent part can
        # run and report partial failures.
        reminder_only = (
            len(preliminary_route.intents) == 1
            and preliminary_route.intent is RequestIntent.REMINDER_ACTION
        )
        if reminder_service is not None and reminder_only:
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
        document_service = context.application.bot_data.get("document_service")
        if document_service is not None:
            lowered = prompt.casefold()
            if re.search(r"\bзабудь\b.*\bдокумент", lowered):
                await message.reply_text("Документ удалён." if document_service.forget(user_id, chat_id) else "Активный документ не найден.")
                return
            if re.search(r"\b(покажи|список)\b.*\bактивн\w* документ", lowered):
                docs=document_service.list_documents(user_id,chat_id)
                text="Активных документов нет." if not docs else "Ваши активные документы:\n"+"\n".join(f"— {x.safe_filename} ({x.document_type.upper()})" for x in docs)
                await message.reply_text(text);return
        typing_task = asyncio.create_task(
            _show_typing(context, update.effective_chat.id)
        )
        try:
            ask_kwargs = dict(user_id=user_id, chat_id=chat_id,
                              source_message_id=getattr(message, "message_id", None),
                              is_allowlisted=bool(principal and principal.role == OWNER),
                              principal=principal, correlation_id=correlation_id)
            if document_service is not None and _should_attach_document_context(
                preliminary_route, prompt
            ):
                page_match=re.search(r"(?i)страниц[аеы]?\s+(\d+)",prompt)
                sheet_match=re.search(r"(?i)лист[ае]?\s+[«\"]?([^»\"?.]+)",prompt)
                doc_context=await asyncio.to_thread(document_service.context,user_id,chat_id,prompt,page=int(page_match.group(1)) if page_match else None,sheet=sheet_match.group(1).strip() if sheet_match else None)
                active_document=document_service.storage.active(user_id,chat_id)
                if active_document and active_document.document_type=="image" and active_document.file_path and active_document.file_path.is_file():
                    ask_kwargs["image_data_url"]=document_service.image.prepare(active_document.file_path,active_document.mime_type)
                elif doc_context: ask_kwargs["document_context"]=doc_context
            thread_id = getattr(message, "message_thread_id", None)
            reply_to = getattr(getattr(message, "reply_to_message", None), "message_id", None)
            if thread_id is not None: ask_kwargs["thread_id"] = thread_id
            if reply_to is not None: ask_kwargs["reply_to_message_id"] = reply_to
            response = await asyncio.wait_for(
                agent.ask(prompt, **ask_kwargs),
                timeout=max(0.1, deadline_at - asyncio.get_running_loop().time()),
            )
        except asyncio.TimeoutError:
            logger.error(
                "request_deadline correlation_id=%s status=timeout code=AGENT_QUEUE_TIMEOUT",
                correlation_id,
            )
            response = (
                "Не удалось завершить запрос вовремя. "
                "Код: AGENT_QUEUE_TIMEOUT"
            )
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
            logger.info("typing_loop correlation_id=%s state=stopped", correlation_id)

        for chunk in _split_message(response):
            try:
                await asyncio.wait_for(
                    message.reply_text(document_service.redact(chunk) if document_service is not None else chunk),
                    timeout=max(0.1, deadline_at - asyncio.get_running_loop().time()),
                )
                logger.info("telegram_response correlation_id=%s status=sent", correlation_id)
            except Exception:
                logger.error("telegram_response correlation_id=%s status=failed code=TELEGRAM_SEND_ERROR", correlation_id)
                raise


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Download only a file identifier received in this trusted update."""
    message=update.effective_message; user=update.effective_user; chat=update.effective_chat
    service=context.application.bot_data.get("document_service")
    if not message or not user or not chat or service is None:return
    attachment=getattr(message,"document",None)
    is_photo=False
    if attachment is None and getattr(message,"photo",None): attachment=message.photo[-1];is_photo=True
    if attachment is None:return
    file_id=attachment.file_id;size=int(getattr(attachment,"file_size",0) or 0)
    filename=(getattr(attachment,"file_name",None) or ("photo.jpg" if is_photo else None))
    mime=(getattr(attachment,"mime_type",None) or ("image/jpeg" if is_photo else mimetypes.guess_type(filename or "")[0]) or "application/octet-stream")
    incoming=service.storage.storage_path/(".incoming-"+uuid.uuid4().hex)
    try:
        if size<=0:raise DocumentError("FILE_SIZE_UNKNOWN","Telegram не сообщил размер файла; безопасная загрузка невозможна.")
        existing=service.storage.by_message(user.id,chat.id,message.message_id)
        if existing:
            await message.reply_text("Этот файл уже обработан.");return
        validated=service.validator.validate_metadata(filename,mime,size)
        telegram_file=await context.bot.get_file(file_id)
        await telegram_file.download_to_drive(custom_path=str(incoming));incoming.chmod(0o600)
        actual=incoming.stat().st_size
        if size and actual!=size:raise DocumentError("SIZE_MISMATCH","Размер скачанного файла не совпадает с данными Telegram.")
        session=await asyncio.to_thread(service.ingest,incoming,user_id=user.id,chat_id=chat.id,message_id=message.message_id,file_id=file_id,filename=filename,mime_type=mime,file_size=actual)
        from app.documents.formatter import received
        caption=(getattr(message,"caption",None) or "").strip()
        if not caption:
            await message.reply_text(received(session));return
        kwargs=dict(user_id=user.id,chat_id=chat.id,source_message_id=message.message_id,is_allowlisted=True,principal=_principal(update,context))
        if session.document_type=="image":
            kwargs["image_data_url"]=service.image.prepare(session.file_path,session.mime_type)
        else:kwargs["document_context"]=await asyncio.to_thread(service.context,user.id,chat.id,caption,session.id)
        response=await context.application.bot_data["agent"].ask(caption,**kwargs)
        await message.reply_text(service.redact(response))
    except (DocumentError, ValidationError) as error:
        incoming.unlink(missing_ok=True);await message.reply_text(getattr(error,"user_message","Не удалось обработать документ."))
    except Exception as error:
        incoming.unlink(missing_ok=True);logger.error("Document processing failed: error_type=%s",type(error).__name__);await message.reply_text("Не удалось безопасно обработать документ.")


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
