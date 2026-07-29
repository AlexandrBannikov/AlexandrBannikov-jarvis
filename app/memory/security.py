"""Secret detection for local project memory."""

from __future__ import annotations

import re


_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\bBearer\s+\S+"),
    re.compile(
        r"(?i)\b(?:TELEGRAM_BOT_TOKEN|OPENAI_API_KEY|PASSWORD|COOKIE|JWT)"
        r"\s*[=:]\s*\S+"
    ),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----"),
    re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    re.compile(r"(?i)(?:^|[/\\])\.env(?:$|[/\\\s])"),
    re.compile(
        r"\b(?:10(?:\.\d{1,3}){3}|192\.168(?:\.\d{1,3}){2}|"
        r"172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
    ),
    re.compile(r"(?i)\bhttps?://(?:localhost|127\.0\.0\.1|[^/\s]+\.internal)\b"),
)


def contains_secret(text: str) -> bool:
    """Return whether text contains a prohibited secret-like value."""
    return any(pattern.search(text) for pattern in _SECRET_PATTERNS)
