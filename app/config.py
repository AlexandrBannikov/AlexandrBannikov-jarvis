"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Config:
    telegram_bot_token: str


def load_config() -> Config:
    """Load and validate application configuration."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")
    return Config(telegram_bot_token=token)
