"""Conservative secret redaction for remote command output and diagnostics."""

from __future__ import annotations

import re

REDACTION = "[REDACTED]"

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
    re.IGNORECASE | re.DOTALL,
)
_AUTH_HEADER = re.compile(
    r"(?im)\b(authorization\s*:\s*)(?:bearer|basic)\s+\S+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}")
_COOKIE = re.compile(r"(?im)\b((?:set-)?cookie\s*:\s*)[^\r\n]+")
_OPENAI_KEY = re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{16,}\b")
_TELEGRAM_TOKEN = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"
)
_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")
_CREDENTIAL_URL = re.compile(
    r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"
)
_ASSIGNMENT = re.compile(
    r"""(?im)^(\s*(?:export\s+)?[A-Za-z0-9_.-]*
        (?:password|passwd|pwd|token|api[_-]?key|secret|access[_-]?key)
        [A-Za-z0-9_.-]*\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s#\r\n]+)""",
    re.VERBOSE,
)
_ENV_ASSIGNMENT = re.compile(
    r"(?im)^(\s*(?:OPENAI_API_KEY|TELEGRAM_BOT_TOKEN|DATABASE_URL|"
    r"AWS_SECRET_ACCESS_KEY|AZURE_CLIENT_SECRET)\s*=\s*)[^\r\n]*"
)


def redact_secrets(text: str) -> str:
    """Redact common credential shapes while retaining ordinary diagnostics."""
    if not text:
        return text
    value = _PRIVATE_KEY.sub(REDACTION, text)
    value = _AUTH_HEADER.sub(lambda match: match.group(1) + REDACTION, value)
    value = _BEARER.sub("Bearer " + REDACTION, value)
    value = _COOKIE.sub(lambda match: match.group(1) + REDACTION, value)
    value = _OPENAI_KEY.sub(REDACTION, value)
    value = _TELEGRAM_TOKEN.sub(REDACTION, value)
    value = _JWT.sub(REDACTION, value)
    value = _AWS_KEY.sub(REDACTION, value)
    value = _CREDENTIAL_URL.sub(lambda match: match.group(1) + REDACTION + "@", value)
    value = _ASSIGNMENT.sub(lambda match: match.group(1) + REDACTION, value)
    return _ENV_ASSIGNMENT.sub(lambda match: match.group(1) + REDACTION, value)
