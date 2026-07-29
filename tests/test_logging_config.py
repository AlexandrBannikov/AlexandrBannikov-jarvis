"""Tests for credential-safe logging."""

import logging

import pytest

from app.logging_config import SanitizingFormatter, sanitize_log_text


@pytest.mark.parametrize(
    "token",
    [
        "123456789:AAExampleTelegramToken",
        "987654321:token-with_under.score",
    ],
)
def test_redacts_arbitrary_telegram_token(token: str) -> None:
    message = (
        f"HTTP Request: POST https://api.telegram.org/bot{token}/getUpdates "
        '"HTTP/1.1 200 OK"'
    )

    sanitized = sanitize_log_text(message)

    assert token not in sanitized
    assert "https://api.telegram.org/bot[REDACTED]/getUpdates" in sanitized
    assert "200 OK" in sanitized


def test_does_not_damage_regular_urls_or_messages() -> None:
    message = "HTTP Request: GET https://example.com/api/status returned 204"

    assert sanitize_log_text(message) == message


@pytest.mark.parametrize(
    ("message", "secret"),
    [
        (
            "Authorization: " + "Bear" + "er accidental-secret-value",
            "accidental-secret-value",
        ),
        (
            "OpenAI key was " + "sk-" + "proj-accidentalSecret123",
            "sk-" + "proj-accidentalSecret123",
        ),
        ("OPENAI_API_KEY=accidental-key-value", "accidental-key-value"),
        ("api_key='accidental-key-value'", "accidental-key-value"),
    ],
)
def test_redacts_openai_credentials(message: str, secret: str) -> None:
    sanitized = sanitize_log_text(message)

    assert secret not in sanitized
    assert "[REDACTED]" in sanitized


def test_secret_absent_from_formatted_parameterized_record() -> None:
    secret = "123456789:AARecordSecretToken"
    record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="HTTP Request: POST %s",
        args=(f"https://api.telegram.org/bot{secret}/getUpdates",),
        exc_info=None,
    )

    rendered = SanitizingFormatter("%(name)s | %(message)s").format(record)

    assert secret not in rendered
    assert "bot[REDACTED]/getUpdates" in rendered
