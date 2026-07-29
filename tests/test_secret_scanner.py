"""Tests for the offline Git secret scanner."""

from scripts.check_secrets import scan_text


def test_secret_scanner_detects_assignments_and_key_material() -> None:
    token_name = "TELEGRAM_" + "BOT_TOKEN"
    private_header = "-----BEGIN " + "OPENSSH PRIVATE KEY-----"
    text = f"{token_name}=not-empty\n{private_header}\n"

    findings = scan_text("example", text)

    assert {finding.kind for finding in findings} == {
        "non-empty secret assignment",
        "private key header",
    }


def test_secret_scanner_allows_empty_templates() -> None:
    token_name = "TELEGRAM_" + "BOT_TOKEN"
    key_name = "OPENAI_" + "API_KEY"

    assert scan_text("template", f"{token_name}=\n{key_name}=\n") == []
