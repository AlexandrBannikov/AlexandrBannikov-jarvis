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
    telegram_error_handler,
    tools_command,
)
from app.tools import create_default_tool_manager

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


def build_application(config: Config) -> Application:
    """Build the Telegram application and register command handlers."""
    application = (
        Application.builder()
        .token(config.telegram_bot_token)
        .post_init(send_startup_notification)
        .build()
    )
    application.bot_data["config"] = config
    ai_client = AIClient(config)
    application.bot_data["ai_client"] = ai_client
    tool_manager = create_default_tool_manager(
        str(config.jarvis_hosts_config)
    )
    application.bot_data["tool_manager"] = tool_manager
    application.bot_data["agent"] = JarvisAgent(
        ai_client.provider,
        tool_manager,
        max_tool_rounds=config.max_tool_rounds,
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
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    application.add_handler(MessageHandler(filters.ALL, handle_unknown))
    application.add_error_handler(telegram_error_handler)
    return application
