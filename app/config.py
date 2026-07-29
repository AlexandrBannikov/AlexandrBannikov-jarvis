"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os
from collections.abc import Mapping
from pathlib import Path


SUPPORTED_LLM_PROVIDERS = frozenset({"openai"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True, slots=True)
class Config:
    telegram_bot_token: str
    llm_provider: str
    openai_api_key: str
    openai_model: str
    openai_base_url: str | None
    log_level: str = "INFO"
    telegram_allowed_user_ids: frozenset[int] = frozenset()
    allow_public_access: bool = False
    jarvis_hosts_config: Path = Path("/etc/jarvis/hosts.yaml")
    max_tool_rounds: int = 4


def _parse_boolean(value: str, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _parse_user_ids(value: str) -> frozenset[int]:
    if not value.strip():
        return frozenset()
    try:
        user_ids = frozenset(int(item.strip()) for item in value.split(","))
    except ValueError as error:
        raise RuntimeError(
            "TELEGRAM_ALLOWED_USER_IDS must contain comma-separated integers"
        ) from error
    if any(user_id <= 0 for user_id in user_ids):
        raise RuntimeError("TELEGRAM_ALLOWED_USER_IDS must contain positive IDs")
    return user_ids


def _parse_max_tool_rounds(value: str) -> int:
    try:
        rounds = int(value)
    except ValueError as error:
        raise RuntimeError("MAX_TOOL_ROUNDS must be an integer") from error
    if not 1 <= rounds <= 10:
        raise RuntimeError("MAX_TOOL_ROUNDS must be between 1 and 10")
    return rounds


def load_config(environment: Mapping[str, str] | None = None) -> Config:
    """Load and validate application configuration."""
    values = os.environ if environment is None else environment
    token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is required")

    provider = values.get("LLM_PROVIDER", "openai").strip().lower()
    if provider not in SUPPORTED_LLM_PROVIDERS:
        raise RuntimeError(f"Unsupported LLM_PROVIDER: {provider}")

    model = values.get("OPENAI_MODEL", "gpt-5.5").strip()
    if provider == "openai" and not model:
        raise RuntimeError("OPENAI_MODEL is required for the OpenAI provider")

    log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in VALID_LOG_LEVELS:
        raise RuntimeError("LOG_LEVEL is invalid")

    allowed_user_ids = _parse_user_ids(
        values.get("TELEGRAM_ALLOWED_USER_IDS", "")
    )
    allow_public_access = _parse_boolean(
        values.get("ALLOW_PUBLIC_ACCESS", "false"), "ALLOW_PUBLIC_ACCESS"
    )
    if not allowed_user_ids and not allow_public_access:
        raise RuntimeError(
            "Set TELEGRAM_ALLOWED_USER_IDS or explicitly set "
            "ALLOW_PUBLIC_ACCESS=true"
        )

    base_url = values.get("OPENAI_BASE_URL", "").strip() or None
    return Config(
        telegram_bot_token=token,
        llm_provider=provider,
        openai_api_key=values.get("OPENAI_API_KEY", "").strip(),
        openai_model=model,
        openai_base_url=base_url,
        log_level=log_level,
        telegram_allowed_user_ids=allowed_user_ids,
        allow_public_access=allow_public_access,
        jarvis_hosts_config=Path(
            values.get(
                "JARVIS_HOSTS_CONFIG", "/etc/jarvis/hosts.yaml"
            ).strip()
            or "/etc/jarvis/hosts.yaml"
        ),
        max_tool_rounds=_parse_max_tool_rounds(
            values.get("MAX_TOOL_ROUNDS", "4").strip()
        ),
    )
