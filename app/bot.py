"""Telegram bot construction."""

from telegram.ext import Application, CommandHandler

from app.config import Config
from app.handlers import help_command, ping, start, status


def build_application(config: Config) -> Application:
    """Build the Telegram application and register command handlers."""
    application = Application.builder().token(config.telegram_bot_token).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("status", status))
    return application
