"""Application configuration loaded from environment variables."""

from dataclasses import dataclass
import os
from collections.abc import Mapping
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SUPPORTED_LLM_PROVIDERS = frozenset({"openai"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
VALID_WEB_SEARCH_CONTEXT_SIZES = frozenset({"low", "medium", "high"})


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
    jarvis_ssh_mode: str = "mock"
    health_host: str = "127.0.0.1"
    health_port: int = 8090
    telegram_startup_notification: bool = False
    web_search_enabled: bool = False
    web_search_context_size: str = "medium"
    memory_enabled: bool = False
    memory_max_context: int = 4_000
    memory_max_context_items: int = 20
    memory_max_results: int = 7
    memory_autosave: bool = True
    memory_summarization: bool = True
    memory_db_path: Path = Path("/var/lib/jarvis/memory.db")
    reminders_enabled: bool = False
    reminders_db_path: Path = Path("/var/lib/jarvis/reminders.db")
    reminders_default_timezone: str = "UTC"
    reminders_poll_interval_seconds: int = 10
    reminders_min_lead_seconds: int = 20
    reminders_max_active_per_user: int = 100
    reminders_max_message_length: int = 1000
    reminders_max_title_length: int = 120
    reminders_max_delivery_attempts: int = 5
    reminders_retry_base_seconds: int = 30
    reminders_overdue_grace_seconds: int = 86400
    reminders_min_recurrence_seconds: int = 3600
    reminders_delivery_enabled: bool = True
    reminders_lease_seconds: int = 120
    reminders_list_limit: int = 20
    ssh_enabled: bool = False
    ssh_servers_config_path: Path = Path("/etc/jarvis/servers.json")
    conversation_state_enabled: bool = True
    conversation_state_ttl_minutes: int = 60
    conversation_history_max_messages: int = 20
    conversation_db_path: Path = Path("/var/lib/jarvis/conversations.db")
    location_enabled: bool = False
    location_db_path: Path = Path("/var/lib/jarvis/location.db")
    access_db_path: Path = Path("/var/lib/jarvis/access.db")
    family_invite_ttl_seconds: int = 86400
    documents_enabled: bool = False
    documents_storage_path: Path = Path("/var/lib/jarvis/documents")
    documents_db_path: Path = Path("/var/lib/jarvis/document_sessions.db")
    documents_max_file_size_mb: int = 20
    documents_max_text_chars: int = 500_000
    documents_max_pdf_pages: int = 300
    documents_max_docx_paragraphs: int = 20_000
    documents_max_spreadsheet_cells: int = 200_000
    documents_max_image_pixels: int = 25_000_000
    documents_session_ttl_hours: int = 24
    documents_max_active_per_user: int = 20
    documents_max_context_chars: int = 50_000
    documents_max_chunks_per_request: int = 12


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


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as error:
        raise RuntimeError("HEALTH_PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise RuntimeError("HEALTH_PORT is outside the allowed range")
    return port


def _parse_bounded_int(
    value: str, name: str, minimum: int, maximum: int
) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be an integer") from error
    if not minimum <= parsed <= maximum:
        raise RuntimeError(f"{name} is outside the allowed range")
    return parsed


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
    ssh_mode = values.get("JARVIS_SSH_MODE", "mock").strip().lower()
    if ssh_mode not in {"mock", "real"}:
        raise RuntimeError("JARVIS_SSH_MODE must be mock or real")
    web_search_context_size = values.get(
        "JARVIS_WEB_SEARCH_CONTEXT_SIZE", "medium"
    ).strip().lower()
    if web_search_context_size not in VALID_WEB_SEARCH_CONTEXT_SIZES:
        raise RuntimeError(
            "JARVIS_WEB_SEARCH_CONTEXT_SIZE must be low, medium or high"
        )
    reminders_timezone = values.get(
        "REMINDERS_DEFAULT_TIMEZONE", "UTC"
    ).strip() or "UTC"
    try:
        ZoneInfo(reminders_timezone)
    except ZoneInfoNotFoundError as error:
        raise RuntimeError("REMINDERS_DEFAULT_TIMEZONE is invalid") from error
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
        jarvis_ssh_mode=ssh_mode,
        health_host=values.get("HEALTH_HOST", "127.0.0.1").strip()
        or "127.0.0.1",
        health_port=_parse_port(values.get("HEALTH_PORT", "8090").strip()),
        telegram_startup_notification=_parse_boolean(
            values.get("TELEGRAM_STARTUP_NOTIFICATION", "false"),
            "TELEGRAM_STARTUP_NOTIFICATION",
        ),
        web_search_enabled=_parse_boolean(
            values.get("JARVIS_WEB_SEARCH_ENABLED", "false"),
            "JARVIS_WEB_SEARCH_ENABLED",
        ),
        web_search_context_size=web_search_context_size,
        memory_enabled=_parse_boolean(
            values.get("MEMORY_ENABLED", "false"), "MEMORY_ENABLED"
        ),
        memory_max_context=_parse_bounded_int(
            values.get("MEMORY_MAX_CONTEXT_CHARS",
                       values.get("MEMORY_MAX_CONTEXT", "4000")).strip(),
            "MEMORY_MAX_CONTEXT_CHARS",
            500,
            20_000,
        ),
        memory_max_context_items=_parse_bounded_int(
            values.get("MEMORY_MAX_CONTEXT_ITEMS", "20").strip(),
            "MEMORY_MAX_CONTEXT_ITEMS", 1, 100,
        ),
        memory_max_results=_parse_bounded_int(
            values.get("MEMORY_MAX_RESULTS", "7").strip(),
            "MEMORY_MAX_RESULTS",
            1,
            10,
        ),
        memory_autosave=_parse_boolean(
            values.get("MEMORY_AUTO_EXTRACT_ENABLED",
                       values.get("MEMORY_AUTOSAVE", "true")),
            "MEMORY_AUTO_EXTRACT_ENABLED"
        ),
        memory_summarization=_parse_boolean(
            values.get("MEMORY_SUMMARIZATION", "true"),
            "MEMORY_SUMMARIZATION",
        ),
        memory_db_path=Path(
            values.get("MEMORY_DB_PATH", "/var/lib/jarvis/memory.db").strip()
            or "/var/lib/jarvis/memory.db"
        ),
        reminders_enabled=_parse_boolean(
            values.get("REMINDERS_ENABLED", "false"), "REMINDERS_ENABLED"
        ),
        reminders_db_path=Path(
            values.get(
                "REMINDERS_DB_PATH", "/var/lib/jarvis/reminders.db"
            ).strip()
            or "/var/lib/jarvis/reminders.db"
        ),
        reminders_default_timezone=reminders_timezone,
        reminders_poll_interval_seconds=_parse_bounded_int(
            values.get("REMINDERS_POLL_INTERVAL_SECONDS", "10"),
            "REMINDERS_POLL_INTERVAL_SECONDS", 1, 3600,
        ),
        reminders_min_lead_seconds=_parse_bounded_int(
            values.get("REMINDERS_MIN_LEAD_SECONDS", "20"),
            "REMINDERS_MIN_LEAD_SECONDS", 1, 86400,
        ),
        reminders_max_active_per_user=_parse_bounded_int(
            values.get("REMINDERS_MAX_ACTIVE_PER_USER", "100"),
            "REMINDERS_MAX_ACTIVE_PER_USER", 1, 1000,
        ),
        reminders_max_message_length=_parse_bounded_int(
            values.get("REMINDERS_MAX_MESSAGE_LENGTH", "1000"),
            "REMINDERS_MAX_MESSAGE_LENGTH", 1, 4000,
        ),
        reminders_max_title_length=_parse_bounded_int(
            values.get("REMINDERS_MAX_TITLE_LENGTH", "120"),
            "REMINDERS_MAX_TITLE_LENGTH", 1, 500,
        ),
        reminders_max_delivery_attempts=_parse_bounded_int(
            values.get("REMINDERS_MAX_DELIVERY_ATTEMPTS", "5"),
            "REMINDERS_MAX_DELIVERY_ATTEMPTS", 1, 20,
        ),
        reminders_retry_base_seconds=_parse_bounded_int(
            values.get("REMINDERS_RETRY_BASE_SECONDS", "30"),
            "REMINDERS_RETRY_BASE_SECONDS", 1, 3600,
        ),
        reminders_overdue_grace_seconds=_parse_bounded_int(
            values.get("REMINDERS_OVERDUE_GRACE_SECONDS", "86400"),
            "REMINDERS_OVERDUE_GRACE_SECONDS", 60, 2_592_000,
        ),
        reminders_min_recurrence_seconds=_parse_bounded_int(
            values.get("REMINDERS_MIN_RECURRENCE_SECONDS", "3600"),
            "REMINDERS_MIN_RECURRENCE_SECONDS", 60, 604800,
        ),
        reminders_delivery_enabled=_parse_boolean(
            values.get("REMINDERS_DELIVERY_ENABLED", "true"),
            "REMINDERS_DELIVERY_ENABLED",
        ),
        ssh_enabled=_parse_boolean(
            values.get("JARVIS_SSH_ENABLED", "false"), "JARVIS_SSH_ENABLED"
        ),
        ssh_servers_config_path=Path(
            values.get(
                "JARVIS_SERVERS_CONFIG", "/etc/jarvis/servers.json"
            ).strip() or "/etc/jarvis/servers.json"
        ),
        reminders_lease_seconds=_parse_bounded_int(
            values.get("REMINDERS_LEASE_SECONDS", "120"),
            "REMINDERS_LEASE_SECONDS", 10, 3600,
        ),
        reminders_list_limit=_parse_bounded_int(
            values.get("REMINDERS_LIST_LIMIT", "20"),
            "REMINDERS_LIST_LIMIT", 1, 100,
        ),
        conversation_state_enabled=_parse_boolean(values.get("CONVERSATION_STATE_ENABLED", "true"), "CONVERSATION_STATE_ENABLED"),
        conversation_state_ttl_minutes=_parse_bounded_int(values.get("CONVERSATION_STATE_TTL_MINUTES", "60"), "CONVERSATION_STATE_TTL_MINUTES", 5, 1440),
        conversation_history_max_messages=_parse_bounded_int(values.get("CONVERSATION_HISTORY_MAX_MESSAGES", "20"), "CONVERSATION_HISTORY_MAX_MESSAGES", 4, 100),
        conversation_db_path=Path(values.get("CONVERSATION_DB_PATH", "/var/lib/jarvis/conversations.db").strip() or "/var/lib/jarvis/conversations.db"),
        location_enabled=_parse_boolean(values.get("LOCATION_ENABLED", "true"), "LOCATION_ENABLED"),
        location_db_path=Path(values.get("LOCATION_DB_PATH", "/var/lib/jarvis/location.db").strip() or "/var/lib/jarvis/location.db"),
        access_db_path=Path(values.get("ACCESS_DB_PATH", "/var/lib/jarvis/access.db").strip() or "/var/lib/jarvis/access.db"),
        family_invite_ttl_seconds=_parse_bounded_int(
            values.get("FAMILY_INVITE_TTL_SECONDS", "86400"),
            "FAMILY_INVITE_TTL_SECONDS", 300, 604800,
        ),
        documents_enabled=_parse_boolean(values.get("DOCUMENTS_ENABLED", "false"), "DOCUMENTS_ENABLED"),
        documents_storage_path=Path(values.get("DOCUMENTS_STORAGE_PATH", "/var/lib/jarvis/documents").strip() or "/var/lib/jarvis/documents"),
        documents_db_path=Path(values.get("DOCUMENTS_DB_PATH", "/var/lib/jarvis/document_sessions.db").strip() or "/var/lib/jarvis/document_sessions.db"),
        documents_max_file_size_mb=_parse_bounded_int(values.get("DOCUMENTS_MAX_FILE_SIZE_MB", "20"), "DOCUMENTS_MAX_FILE_SIZE_MB", 1, 100),
        documents_max_text_chars=_parse_bounded_int(values.get("DOCUMENTS_MAX_TEXT_CHARS", "500000"), "DOCUMENTS_MAX_TEXT_CHARS", 1000, 5000000),
        documents_max_pdf_pages=_parse_bounded_int(values.get("DOCUMENTS_MAX_PDF_PAGES", "300"), "DOCUMENTS_MAX_PDF_PAGES", 1, 2000),
        documents_max_docx_paragraphs=_parse_bounded_int(values.get("DOCUMENTS_MAX_DOCX_PARAGRAPHS", "20000"), "DOCUMENTS_MAX_DOCX_PARAGRAPHS", 1, 100000),
        documents_max_spreadsheet_cells=_parse_bounded_int(values.get("DOCUMENTS_MAX_SPREADSHEET_CELLS", "200000"), "DOCUMENTS_MAX_SPREADSHEET_CELLS", 1, 2000000),
        documents_max_image_pixels=_parse_bounded_int(values.get("DOCUMENTS_MAX_IMAGE_PIXELS", "25000000"), "DOCUMENTS_MAX_IMAGE_PIXELS", 10000, 100000000),
        documents_session_ttl_hours=_parse_bounded_int(values.get("DOCUMENTS_SESSION_TTL_HOURS", "24"), "DOCUMENTS_SESSION_TTL_HOURS", 1, 720),
        documents_max_active_per_user=_parse_bounded_int(values.get("DOCUMENTS_MAX_ACTIVE_PER_USER", "20"), "DOCUMENTS_MAX_ACTIVE_PER_USER", 1, 100),
        documents_max_context_chars=_parse_bounded_int(values.get("DOCUMENTS_MAX_CONTEXT_CHARS", "50000"), "DOCUMENTS_MAX_CONTEXT_CHARS", 1000, 200000),
        documents_max_chunks_per_request=_parse_bounded_int(values.get("DOCUMENTS_MAX_CHUNKS_PER_REQUEST", "12"), "DOCUMENTS_MAX_CHUNKS_PER_REQUEST", 1, 50),
    )
