"""Tests for the production configuration checker."""

from pathlib import Path

from scripts.check_config import check_config


def write_env(path: Path, *, api_key: str = "test-openai-key") -> None:
    path.write_text(
        "\n".join(
            [
                "TELEGRAM_BOT_TOKEN=test-telegram-token",
                "LLM_PROVIDER=openai",
                f"OPENAI_API_KEY={api_key}",
                "OPENAI_MODEL=test-model",
                "OPENAI_BASE_URL=",
                "LOG_LEVEL=INFO",
                "TELEGRAM_ALLOWED_USER_IDS=123456789",
                "ALLOW_PUBLIC_ACCESS=false",
            ]
        ),
        encoding="utf-8",
    )


def test_check_config_accepts_valid_file(
    tmp_path: Path, capsys
) -> None:
    env_file = tmp_path / "jarvis.env"
    write_env(env_file)

    assert check_config(env_file) == 0

    output = capsys.readouterr().out
    assert "TELEGRAM_BOT_TOKEN: configured" in output
    assert "OPENAI_API_KEY: configured" in output
    assert "OPENAI_MODEL: configured" in output
    assert "Configuration valid" in output
    assert "test-telegram-token" not in output
    assert "test-openai-key" not in output


def test_check_config_rejects_missing_secret(
    tmp_path: Path, capsys
) -> None:
    env_file = tmp_path / "jarvis.env"
    write_env(env_file, api_key="")

    assert check_config(env_file) != 0

    output = capsys.readouterr().out
    assert "OPENAI_API_KEY: missing" in output
    assert "Configuration invalid" in output


def test_check_config_rejects_missing_file(
    tmp_path: Path, capsys
) -> None:
    assert check_config(tmp_path / "missing.env") != 0

    assert "Configuration invalid" in capsys.readouterr().out
