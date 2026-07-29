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
