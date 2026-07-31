"""Telegram bot construction."""

import logging

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.ai.client import AIClient
from app.ai.agent import JarvisAgent
from app.config import Config
from app.handlers import (
    authorize,
    handle_text,
    handle_unknown,
    health_command,
    help_command,
    log_incoming_update,
    ping,
    run_tool,
    start,
    status,
    memory_command,
    memory_projects_command,
    memory_forget_command,
    memory_status_command,
    skills_command,
    telegram_error_handler,
    tools_command,
)
from app.memory import MemoryManager, MemoryStorage
from app.memory.tools import register_memory_tools
from app.reminders import ReminderScheduler, ReminderService, ReminderStorage
from app.reminders.delivery import ReminderDelivery
from app.reminders.tools import register_reminder_tools
from app.health import set_reminder_health_provider, set_ssh_health_provider, set_skill_health_provider
from app.skills.builtin import build_skill_registry
from app.tools import create_default_tool_manager
from app.ssh_agent.bootstrap import build_ssh_dependencies

logger = logging.getLogger(__name__)


async def send_startup_notification(application: Application) -> None:
    """Send one best-effort notification to the first allowlisted user."""
    config = application.bot_data["config"]
    if (
        not config.telegram_startup_notification
        or not config.telegram_allowed_user_ids
    ):
        return
    user_id = min(config.telegram_allowed_user_ids)
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text="Jarvis запущен и готов к работе.",
        )
    except Exception as error:
        logger.warning(
            "Startup notification failed: %s", type(error).__name__
        )


async def initialize_application(application: Application) -> None:
    await send_startup_notification(application)
    scheduler = application.bot_data.get("reminder_scheduler")
    if scheduler is not None:
        scheduler.start()


async def shutdown_application(application: Application) -> None:
    scheduler = application.bot_data.get("reminder_scheduler")
    if scheduler is not None:
        await scheduler.stop()


def build_application(config: Config) -> Application:
    """Build the Telegram application and register command handlers."""
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(initialize_application)
        .post_shutdown(shutdown_application)
        .build()
    )
    application.bot_data["config"] = config
    ai_client = AIClient(config)
    application.bot_data["ai_client"] = ai_client
    tool_manager = create_default_tool_manager(
        str(config.jarvis_hosts_config),
        include_legacy_remote=False,
    )
    memory_manager = None
    if config.memory_enabled:
        memory_manager = MemoryManager(
            MemoryStorage(config.memory_db_path),
            max_results=config.memory_max_results,
            max_context=config.memory_max_context,
            autosave=config.memory_autosave,
            summarization=config.memory_summarization,
            max_context_items=config.memory_max_context_items,
        )
        register_memory_tools(tool_manager.registry, memory_manager)
    application.bot_data["tool_manager"] = tool_manager
    application.bot_data["memory_manager"] = memory_manager
    ssh_dependencies = build_ssh_dependencies(
        enabled=config.ssh_enabled,
        config_path=config.ssh_servers_config_path,
        tool_registry=tool_manager.registry,
    )
    application.bot_data["ssh_service"] = ssh_dependencies.service
    application.bot_data["ssh_dependencies"] = ssh_dependencies
    set_ssh_health_provider(ssh_dependencies)
    reminder_service = None
    reminder_scheduler = None
    if config.reminders_enabled:
        reminder_storage = ReminderStorage(config.reminders_db_path)
        reminder_service = ReminderService(
            reminder_storage,
            default_timezone=config.reminders_default_timezone,
            min_lead_seconds=config.reminders_min_lead_seconds,
            max_active_per_user=config.reminders_max_active_per_user,
            max_title_length=config.reminders_max_title_length,
            max_message_length=config.reminders_max_message_length,
            min_recurrence_seconds=config.reminders_min_recurrence_seconds,
            list_limit=config.reminders_list_limit,
        )
        register_reminder_tools(tool_manager.registry, reminder_service)
        reminder_scheduler = ReminderScheduler(
            reminder_storage,
            ReminderDelivery(
                application.bot,
                config.telegram_allowed_user_ids,
                enabled=config.reminders_delivery_enabled,
            ),
            poll_interval=config.reminders_poll_interval_seconds,
            lease_seconds=config.reminders_lease_seconds,
            max_attempts=config.reminders_max_delivery_attempts,
            retry_base_seconds=config.reminders_retry_base_seconds,
            overdue_grace_seconds=config.reminders_overdue_grace_seconds,
        )
    application.bot_data["reminder_service"] = reminder_service
    application.bot_data["reminder_scheduler"] = reminder_scheduler
    set_reminder_health_provider(
        (lambda: reminder_scheduler) if reminder_scheduler else None,
        enabled=config.reminders_enabled,
        storage=reminder_service.storage if reminder_service else None,
    )
    skill_registry = build_skill_registry(
        tool_manager.registry,
        config,
        memory_manager=memory_manager,
        reminder_service=reminder_service,
        reminder_scheduler=reminder_scheduler,
        ssh_dependencies=ssh_dependencies,
    )
    required_errors = skill_registry.required_errors()
    if required_errors:
        raise RuntimeError("Required Skills Registry capability is unhealthy")
    summary = skill_registry.summary()
    logger.info(
        "skills initialized total=%s ok=%s warning=%s error=%s disabled=%s",
        summary["total"], summary["ok"], summary["warning"],
        summary["error"], summary["disabled"],
    )
    application.bot_data["skill_registry"] = skill_registry
    set_skill_health_provider(skill_registry)
    application.bot_data["agent"] = JarvisAgent(
        ai_client.provider,
        tool_manager,
        max_tool_rounds=config.max_tool_rounds,
        web_search_enabled=config.web_search_enabled,
        web_search_context_size=config.web_search_context_size,
        memory_manager=memory_manager,
    )
    application.bot_data["user_locks"] = {}
    application.add_handler(
        MessageHandler(filters.ALL, log_incoming_update), group=-2
    )
    application.add_handler(MessageHandler(filters.ALL, authorize), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("health", health_command))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("tool", run_tool))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(CommandHandler("skills", skills_command))
    application.add_handler(CommandHandler("memory", memory_command))
    application.add_handler(CommandHandler("memory_projects", memory_projects_command))
    application.add_handler(CommandHandler("memory_forget", memory_forget_command))
    application.add_handler(CommandHandler("memory_status", memory_status_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    application.add_handler(MessageHandler(filters.ALL, handle_unknown))
    application.add_error_handler(telegram_error_handler)
    return application
