import pytest

from app.ssh_agent.redaction import REDACTION, redact_secrets


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "proj-abcdefghijklmnopqrstuvwxyz123456",
        "123456789:" + "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
        "Bearer abcdefghijklmnopqrstuvwxyz",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signaturevalue",
        "password=supersecret",
        "Cookie: session=supersecret",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----",
        "OPENAI_API_KEY=supersecret",
        "postgresql://admin:password@database.internal/app",
        "AKIAABCDEFGHIJKLMNOP",
        "api_key: abcdefghijklmnop",
    ],
)
def test_secret_shapes_are_redacted(secret: str) -> None:
    output = redact_secrets(secret)
    assert REDACTION in output
    assert "supersecret" not in output


def test_normal_journal_output_remains_readable() -> None:
    value = "2026-07-30T10:00:00Z app[42]: worker completed job 17\n"
    assert redact_secrets(value) == value
