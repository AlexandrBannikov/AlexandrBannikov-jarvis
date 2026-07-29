"""Network-free tests for the Paramiko SSH adapter."""

from io import StringIO
from pathlib import Path
import socket
from unittest.mock import Mock

import paramiko
import pytest

from app.infrastructure.errors import (
    AuthenticationFailedError,
    ChangedHostKeyError,
    CommandTimeoutError,
    ConnectionFailedError,
    OutputTooLargeError,
    ServiceNotAllowedError,
    UnknownHostKeyError,
)
from app.infrastructure.hosts import HostConfig
from app.infrastructure.ssh_client import SSHClient


def host(timeout: float = 1) -> HostConfig:
    return HostConfig(
        alias="crypto",
        hostname="192.0.2.10",
        port=22,
        username="jarvis-monitor",
        identity_file=Path("/private/key"),
        known_hosts_file=Path("/known_hosts"),
        connect_timeout_seconds=1,
        command_timeout_seconds=timeout,
        allowed_services=frozenset({"safe.service"}),
    )


class FakeChannel:
    def __init__(
        self, stdout: bytes = b"{}", stderr: bytes = b"", exit_code: int = 0
    ) -> None:
        self.stdout = bytearray(stdout)
        self.stderr = bytearray(stderr)
        self.exit_code = exit_code
        self.closed = False
        self.shutdown = False

    def recv_ready(self) -> bool:
        return bool(self.stdout)

    def recv(self, size: int) -> bytes:
        data = bytes(self.stdout[:size])
        del self.stdout[:size]
        return data

    def recv_stderr_ready(self) -> bool:
        return bool(self.stderr)

    def recv_stderr(self, size: int) -> bytes:
        data = bytes(self.stderr[:size])
        del self.stderr[:size]
        return data

    def exit_status_ready(self) -> bool:
        return not self.stdout and not self.stderr

    def recv_exit_status(self) -> int:
        return self.exit_code

    def shutdown_write(self) -> None:
        self.shutdown = True

    def close(self) -> None:
        self.closed = True


class FakeStream(StringIO):
    def __init__(self, channel: FakeChannel) -> None:
        super().__init__()
        self.channel = channel


class FakeClient:
    def __init__(self, channel: FakeChannel | None = None) -> None:
        self.channel = channel or FakeChannel()
        self.policy = None
        self.connect_kwargs = {}
        self.command = None
        self.command_kwargs = {}
        self.closed = False

    def load_host_keys(self, filename: str) -> None:
        self.known_hosts = filename

    def set_missing_host_key_policy(self, policy: object) -> None:
        self.policy = policy

    def connect(self, **kwargs: object) -> None:
        self.connect_kwargs = kwargs

    def exec_command(self, command: str, **kwargs: object):
        self.command = command
        self.command_kwargs = kwargs
        return (
            FakeStream(self.channel),
            FakeStream(self.channel),
            FakeStream(self.channel),
        )

    def close(self) -> None:
        self.closed = True


def test_secure_connection_options_and_no_auto_add_policy() -> None:
    fake = FakeClient()

    SSHClient(host(), client_factory=lambda: fake).run_system_info()

    assert not isinstance(
        fake.policy, (paramiko.AutoAddPolicy, paramiko.WarningPolicy)
    )
    assert fake.connect_kwargs["allow_agent"] is False
    assert fake.connect_kwargs["look_for_keys"] is False
    assert fake.connect_kwargs["password"] is None
    assert fake.command_kwargs["get_pty"] is False
    assert fake.command == "python3 -"
    assert fake.closed is True


def test_unknown_host_key_is_rejected() -> None:
    fake = FakeClient()

    def connect(**kwargs: object) -> None:
        del kwargs
        fake.policy.missing_host_key(fake, "host", Mock())

    fake.connect = connect  # type: ignore[method-assign]

    with pytest.raises(UnknownHostKeyError):
        SSHClient(host(), client_factory=lambda: fake).run_system_info()
    assert fake.closed is True


@pytest.mark.parametrize(
    ("source_error", "expected"),
    [
        (
            paramiko.BadHostKeyException("host", Mock(), Mock()),
            ChangedHostKeyError,
        ),
        (paramiko.AuthenticationException(), AuthenticationFailedError),
        (socket.timeout(), ConnectionFailedError),
    ],
)
def test_connection_errors_are_sanitized(
    source_error: Exception, expected: type[Exception]
) -> None:
    fake = FakeClient()
    fake.connect = Mock(side_effect=source_error)

    with pytest.raises(expected):
        SSHClient(host(), client_factory=lambda: fake).run_system_info()
    assert fake.closed is True


def test_output_size_is_limited_and_connection_closed() -> None:
    fake = FakeClient(FakeChannel(stdout=b"x" * 20))

    with pytest.raises(OutputTooLargeError):
        SSHClient(
            host(), max_output_bytes=10, client_factory=lambda: fake
        ).run_system_info()

    assert fake.closed is True


def test_command_timeout_closes_channel_and_connection() -> None:
    channel = FakeChannel()
    channel.exit_status_ready = lambda: False  # type: ignore[method-assign]
    fake = FakeClient(channel)

    with pytest.raises(CommandTimeoutError):
        SSHClient(
            host(timeout=0.001), client_factory=lambda: fake
        ).run_system_info()

    assert channel.closed is True
    assert fake.closed is True


def test_service_command_is_allowlisted_and_fixed() -> None:
    fake = FakeClient(FakeChannel(stdout=b"Id=safe.service\n"))

    result = SSHClient(
        host(), client_factory=lambda: fake
    ).run_service_status("safe.service")

    assert result.command_name == "service_status"
    assert fake.command.startswith("systemctl show --no-pager --property=")
    assert fake.command.endswith(" safe.service")


@pytest.mark.parametrize(
    "service", ["other.service", "safe.service;id", "$(id)", "../safe.service"]
)
def test_service_name_injection_and_non_allowlisted_units_are_rejected(
    service: str,
) -> None:
    with pytest.raises(ServiceNotAllowedError):
        SSHClient(host(), client_factory=Mock).run_service_status(service)
