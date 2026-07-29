"""Tests for environment-based configuration."""

import pytest

from app.config import load_config


def test_load_config_reads_token_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")

    config = load_config()

    assert config.telegram_bot_token == "test-token"
    assert config.llm_provider == "openai"
    assert config.openai_api_key == "openai-key"
    assert config.openai_model == "test-model"
    assert config.openai_base_url == "https://example.test/v1"


def test_load_config_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "  test-token  ")

    assert load_config().telegram_bot_token == "test-token"


def test_load_config_requires_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        load_config()


def test_load_config_uses_llm_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    for name in ("LLM_PROVIDER", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(name, raising=False)

    config = load_config()

    assert config.llm_provider == "openai"
    assert config.openai_api_key == ""
    assert config.openai_model == "gpt-5.5"
    assert config.openai_base_url is None
