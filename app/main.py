"""Jarvis Telegram bot entry point."""

import logging
from pathlib import Path
import sys

from app.bot import build_application
from app.config import load_config

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(log_level: str = "INFO") -> None:
    """Write application logs to stdout and logs/jarvis.log."""
    log_directory = Path("logs")
    log_directory.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level),
        format=LOG_FORMAT,
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(log_directory / "jarvis.log", encoding="utf-8"),
        ],
        force=True,
    )


def main() -> None:
    """Start Jarvis using Telegram long polling."""
    config = load_config()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    application = build_application(config)
    logger.info("Starting Jarvis Telegram bot")
    application.run_polling()


if __name__ == "__main__":
    main()
