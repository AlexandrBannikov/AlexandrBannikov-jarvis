"""Tests for environment-based configuration."""

from pathlib import Path

import pytest

from app.config import load_config


def test_load_config_reads_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123,456")

    config = load_config()

    assert config.telegram_bot_token == "test-token"
    assert config.llm_provider == "openai"
    assert config.openai_api_key == "openai-key"
    assert config.openai_model == "test-model"
    assert config.openai_base_url == "https://example.test/v1"
    assert config.telegram_allowed_user_ids == frozenset({123, 456})
    assert config.allow_public_access is False
    assert config.jarvis_hosts_config == Path("/etc/jarvis/hosts.yaml")
    assert config.max_tool_rounds == 4


def test_load_config_parses_production_user_id_as_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "228333796")

    user_ids = load_config().telegram_allowed_user_ids

    assert user_ids == frozenset({228333796})
    assert all(isinstance(user_id, int) for user_id in user_ids)


def test_load_config_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  test-token  ")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")

    assert load_config().telegram_bot_token == "test-token"


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_config()


def test_load_config_uses_llm_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    for name in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.llm_provider == "openai"
    assert config.openai_api_key == ""
    assert config.openai_model == "gpt-5.5"
    assert config.openai_base_url is None
    assert config.web_search_enabled is False
    assert config.web_search_context_size == "medium"
    assert config.memory_enabled is False
    assert config.memory_max_context == 4_000
    assert config.memory_max_results == 7
    assert config.memory_autosave is True
    assert config.memory_summarization is True
    assert config.memory_db_path == Path("/var/lib/jarvis/memory.db")
    assert config.memory_max_context_items == 20
    assert config.reminders_enabled is False
    assert config.reminders_default_timezone == "UTC"
    assert config.reminders_db_path == Path("/var/lib/jarvis/reminders.db")


def test_load_config_reads_reminder_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("REMINDERS_ENABLED", "true")
    monkeypatch.setenv("REMINDERS_DEFAULT_TIMEZONE", "Asia/Yekaterinburg")
    monkeypatch.setenv("REMINDERS_DB_PATH", "/tmp/reminders.db")
    monkeypatch.setenv("REMINDERS_POLL_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("REMINDERS_MAX_DELIVERY_ATTEMPTS", "7")
    config = load_config()
    assert config.reminders_enabled
    assert config.reminders_default_timezone == "Asia/Yekaterinburg"
    assert config.reminders_db_path == Path("/tmp/reminders.db")
    assert config.reminders_poll_interval_seconds == 15
    assert config.reminders_max_delivery_attempts == 7


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("REMINDERS_DEFAULT_TIMEZONE", "Invalid/Zone"),
        ("REMINDERS_POLL_INTERVAL_SECONDS", "0"),
        ("REMINDERS_MAX_DELIVERY_ATTEMPTS", "0"),
        ("REMINDERS_MIN_RECURRENCE_SECONDS", "59"),
        ("REMINDERS_LEASE_SECONDS", "9"),
    ],
)
def test_load_config_rejects_invalid_reminder_settings(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        load_config()


def test_load_config_reads_web_search_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("JARVIS_WEB_SEARCH_ENABLED", "true")
    monkeypatch.setenv("JARVIS_WEB_SEARCH_CONTEXT_SIZE", "high")

    config = load_config()

    assert config.web_search_enabled is True
    assert config.web_search_context_size == "high"


def test_load_config_rejects_invalid_web_search_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("JARVIS_WEB_SEARCH_CONTEXT_SIZE", "huge")

    with pytest.raises(RuntimeError, match="JARVIS_WEB_SEARCH_CONTEXT_SIZE"):
        load_config()


def test_load_config_reads_memory_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("MEMORY_ENABLED", "true")
    monkeypatch.setenv("MEMORY_MAX_CONTEXT", "8000")
    monkeypatch.setenv("MEMORY_MAX_RESULTS", "5")
    monkeypatch.setenv("MEMORY_AUTOSAVE", "false")
    monkeypatch.setenv("MEMORY_SUMMARIZATION", "false")
    monkeypatch.setenv("MEMORY_DB_PATH", "/tmp/test-memory.db")

    config = load_config()

    assert config.memory_enabled is True
    assert config.memory_max_context == 8_000
    assert config.memory_max_results == 5
    assert config.memory_autosave is False
    assert config.memory_summarization is False
    assert config.memory_db_path == Path("/tmp/test-memory.db")


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("MEMORY_MAX_CONTEXT", "499"),
        ("MEMORY_MAX_CONTEXT", "20001"),
        ("MEMORY_MAX_RESULTS", "0"),
        ("MEMORY_MAX_RESULTS", "11"),
        ("MEMORY_MAX_RESULTS", "invalid"),
    ],
)
def test_load_config_rejects_invalid_memory_limits(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        load_config()


def test_load_config_requires_explicit_access_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.delenv("TELEGRAM_ALLOWED_USER_IDS", raising=False)
    monkeypatch.delenv("ALLOW_PUBLIC_ACCESS", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_ALLOWED_USER_IDS"):
        load_config()


def test_load_config_rejects_invalid_user_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123,not-an-id")

    with pytest.raises(RuntimeError, match="comma-separated integers"):
        load_config()


def test_load_config_rejects_unknown_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("LLM_PROVIDER", "unknown")

    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER"):
        load_config()


def test_load_config_reads_hosts_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("JARVIS_HOSTS_CONFIG", "/safe/hosts.yaml")

    assert load_config().jarvis_hosts_config == Path("/safe/hosts.yaml")


def test_load_config_reads_max_tool_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("MAX_TOOL_ROUNDS", "6")

    assert load_config().max_tool_rounds == 6


def test_load_config_reads_rollout_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.setenv("JARVIS_SSH_MODE", "real")
    monkeypatch.setenv("HEALTH_HOST", "127.0.0.2")
    monkeypatch.setenv("HEALTH_PORT", "9000")
    monkeypatch.setenv("TELEGRAM_STARTUP_NOTIFICATION", "true")

    config = load_config()

    assert config.jarvis_ssh_mode == "real"
    assert config.health_host == "127.0.0.2"
    assert config.health_port == 9000
    assert config.telegram_startup_notification is True


def test_load_config_rejects_invalid_ssh_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123")
    monkeypatch.setenv("JARVIS_SSH_MODE", "unsafe")

    with pytest.raises(RuntimeError, match="JARVIS_SSH_MODE"):
        load_config()


@pytest.mark.parametrize("value", ["0", "11", "not-a-number"])
def test_load_config_rejects_invalid_max_tool_rounds(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("ALLOW_PUBLIC_ACCESS", "true")
    monkeypatch.setenv("MAX_TOOL_ROUNDS", value)

    with pytest.raises(RuntimeError, match="MAX_TOOL_ROUNDS"):
        load_config()
