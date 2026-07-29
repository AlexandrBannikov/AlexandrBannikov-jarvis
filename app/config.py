"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os


@dataclass(frozen=True, slots=True)
class Config:
    telegram_bot_token: str
    llm_provider: str
    openai_api_key: str
    openai_model: str
    openai_base_url: str | None


def load_config() -> Config:
    """Load and validate application configuration."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")
    provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower()
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5").strip()
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
    return Config(
        telegram_bot_token=token,
        llm_provider=provider,
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=model,
        openai_base_url=base_url,
    )
