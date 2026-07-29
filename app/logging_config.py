"""Logging helpers that prevent credentials from reaching log destinations."""

from __future__ import annotations

import logging
import re


_SECRET_PATTERNS = (
    # Telegram embeds the bot credential in the API path.
    (
        re.compile(r"(?i)(/bot)[^/\s?#]+(?=/)"),
        r"\1[REDACTED]",
    ),
    # Authorization headers occasionally appear in HTTP client diagnostics.
    (
        re.compile(r"(?i)\b(Bearer)\s+[A-Za-z0-9._~+/=-]+"),
        r"\1 [REDACTED]",
    ),
    # OpenAI keys can also appear outside an Authorization header.
    (
        re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b"),
        "[REDACTED]",
    ),
    (
        re.compile(
            r"(?i)\b(OPENAI_API_KEY|api[_-]?key)(\s*[=:]\s*)"
            r"([\"']?)[^\s,\"'}]+"
        ),
        r"\1\2[REDACTED]",
    ),
)


def sanitize_log_text(value: object) -> str:
    """Return a printable value with supported credential forms redacted."""
    sanitized = str(value)
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


class SanitizingFormatter(logging.Formatter):
    """Apply redaction to the complete rendered record.

    Sanitizing after standard formatting covers parameterized messages,
    exception tracebacks, and records emitted by third-party libraries.
    """

    def format(self, record: logging.LogRecord) -> str:
        return sanitize_log_text(super().format(record))
