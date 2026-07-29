"""Tests for strict remote host configuration."""

from pathlib import Path

import pytest

from app.infrastructure.errors import HostsConfigurationError, UnknownHostError
from app.infrastructure.hosts import load_hosts_config


VALID = """
known_hosts_file: /etc/jarvis/known_hosts
hosts:
  crypto:
    hostname: 192.0.2.10
    port: 22
    username: jarvis-monitor
    identity_file: /etc/jarvis/keys/crypto
    connect_timeout_seconds: 10
    command_timeout_seconds: 15
    allowed_services: [crypto.timer, crypto.service]
"""


def write_config(tmp_path: Path, text: str = VALID) -> Path:
    path = tmp_path / "hosts.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_valid_hosts_config(tmp_path: Path) -> None:
    config = load_hosts_config(write_config(tmp_path), required=True)
    host = config.get("crypto")

    assert host.hostname == "192.0.2.10"
    assert host.port == 22
    assert host.username == "jarvis-monitor"
    assert host.identity_file == Path("/etc/jarvis/keys/crypto")
    assert host.allowed_services == frozenset(
        {"crypto.timer", "crypto.service"}
    )


def test_missing_optional_config_produces_empty_registry(tmp_path: Path) -> None:
    config = load_hosts_config(tmp_path / "missing.yaml")

    with pytest.raises(UnknownHostError):
        config.get("missing")


@pytest.mark.parametrize(
    "text",
    [
        VALID + "\nunknown: true\n",
        VALID.replace("    port: 22\n", "    port: 22\n    surprise: true\n"),
        VALID.replace("jarvis-monitor", "root"),
        VALID.replace("/etc/jarvis/keys/crypto", "keys/crypto"),
        VALID.replace("port: 22", "port: 0"),
        VALID.replace("port: 22", "port: 65536"),
        VALID.replace("connect_timeout_seconds: 10", "connect_timeout_seconds: 0"),
        VALID.replace("command_timeout_seconds: 15", "command_timeout_seconds: 301"),
        VALID.replace("hostname: 192.0.2.10", "hostname: ''"),
        VALID.replace("  crypto:", "  '':"),
        VALID.replace(
            "  crypto:\n",
            "  crypto:\n    hostname: duplicate.example\n",
        ),
    ],
)
def test_rejects_invalid_configuration(tmp_path: Path, text: str) -> None:
    with pytest.raises(HostsConfigurationError):
        load_hosts_config(write_config(tmp_path, text), required=True)


def test_rejects_duplicate_host_alias(tmp_path: Path) -> None:
    text = VALID + VALID.split("hosts:\n", 1)[1]

    with pytest.raises(HostsConfigurationError):
        load_hosts_config(write_config(tmp_path, text), required=True)


def test_host_specific_known_hosts_override(tmp_path: Path) -> None:
    text = VALID.replace(
        "    identity_file:",
        "    known_hosts_file: /etc/jarvis/crypto_known_hosts\n"
        "    identity_file:",
    )

    host = load_hosts_config(write_config(tmp_path, text)).get("crypto")

    assert host.known_hosts_file == Path("/etc/jarvis/crypto_known_hosts")
