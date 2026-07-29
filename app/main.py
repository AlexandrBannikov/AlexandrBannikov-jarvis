"""Jarvis Telegram bot entry point."""

import logging
from pathlib import Path
import sys

from app.bot import build_application
from app.config import load_config
from app.health import HealthServer
from app.startup import startup_self_check

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(log_level: str = "INFO") -> None:
    """Configure application and sanitized remote-operation audit logs."""
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
    audit_handler = logging.FileHandler(
        log_directory / "audit.log", encoding="utf-8"
    )
    audit_handler.setFormatter(logging.Formatter(LOG_FORMAT))
    audit_logger = logging.getLogger("jarvis.audit")
    audit_logger.handlers.clear()
    audit_logger.addHandler(audit_handler)
    audit_logger.setLevel(logging.INFO)
    audit_logger.propagate = False


def main() -> None:
    """Start Jarvis using Telegram long polling."""
    config = load_config()
    configure_logging(config.log_level)
    logger = logging.getLogger(__name__)
    startup_self_check(config)
    health_server = HealthServer(config.health_host, config.health_port)
    health_server.start()
    application = build_application(config)
    logger.info("Starting Jarvis Telegram bot")
    try:
        application.run_polling()
    finally:
        health_server.stop()


if __name__ == "__main__":
    main()
