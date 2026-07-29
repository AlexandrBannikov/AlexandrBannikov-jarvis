"""Telegram bot construction."""

from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.ai.client import AIClient
from app.ai.agent import JarvisAgent
from app.config import Config
from app.handlers import (
    authorize,
    handle_text,
    help_command,
    ping,
    run_tool,
    start,
    status,
    tools_command,
)
from app.tools import create_default_tool_manager


def build_application(config: Config) -> Application:
    """Build the Telegram application and register command handlers."""
    application = Application.builder().token(config.telegram_bot_token).build()
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
    application.add_handler(MessageHandler(filters.ALL, authorize), group=-1)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("tool", run_tool))
    application.add_handler(CommandHandler("tools", tools_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)
    )
    return application
