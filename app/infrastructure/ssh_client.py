"""Paramiko adapter limited to fixed read-only diagnostic operations."""

from dataclasses import dataclass
import socket
import time
from typing import Final

import paramiko

from app.infrastructure.errors import (
    AuthenticationFailedError,
    ChangedHostKeyError,
    CommandTimeoutError,
    ConnectionFailedError,
    OutputTooLargeError,
    UnknownHostKeyError,
)
from app.infrastructure.hosts import HostConfig, SERVICE_PATTERN

DEFAULT_MAX_OUTPUT_BYTES: Final = 1_048_576
_SYSTEM_INFO_COMMAND: Final = "python3 -"
_SYSTEM_INFO_SCRIPT: Final = r"""
import datetime
import json
import os
import platform
import shutil
import socket

memory = {}
with open("/proc/meminfo", encoding="ascii") as source:
    for line in source:
        key, value = line.split(":", 1)
        memory[key] = int(value.strip().split()[0]) * 1024
disk = shutil.disk_usage("/")
with open("/proc/uptime", encoding="ascii") as source:
    uptime = float(source.read().split()[0])
os_name = platform.platform()
try:
    with open("/etc/os-release", encoding="utf-8") as source:
        entries = dict(
            line.rstrip().split("=", 1)
            for line in source
            if "=" in line
        )
    os_name = entries.get("PRETTY_NAME", os_name).strip('"')
except OSError:
    pass
print(json.dumps({
    "hostname": socket.gethostname(),
    "kernel": platform.release(),
    "os": os_name,
    "architecture": platform.machine(),
    "uptime_seconds": uptime,
    "cpu_count": os.cpu_count(),
    "memory_total_bytes": memory.get("MemTotal", 0),
    "memory_available_bytes": memory.get("MemAvailable", 0),
    "disk_total_bytes": disk.total,
    "disk_available_bytes": disk.free,
    "current_utc_time": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(timespec="seconds"),
}, separators=(",", ":")))
"""
_SERVICE_PROPERTIES: Final = (
    "Id,Description,LoadState,ActiveState,SubState,UnitFileState,MainPID,"
    "ExecMainStatus,ActiveEnterTimestamp,InactiveEnterTimestamp"
)


@dataclass(frozen=True, slots=True)
class SSHCommandResult:
    host: str
    command_name: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: float
    timed_out: bool


class _RejectUnknownHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        del client, hostname, key
        raise UnknownHostKeyError("SSH host key is not trusted")


class SSHClient:
    """Execute only built-in remote monitoring commands."""

    def __init__(
        self,
        host: HostConfig,
        *,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        client_factory: type[paramiko.SSHClient] = paramiko.SSHClient,
    ) -> None:
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self.host = host
        self.max_output_bytes = max_output_bytes
        self._client_factory = client_factory

    def run_system_info(self) -> SSHCommandResult:
        return self._execute(
            "system_info", _SYSTEM_INFO_COMMAND, stdin_data=_SYSTEM_INFO_SCRIPT
        )

    def test_connection(self) -> bool:
        """Open and close an authenticated SSH connection without a command."""
        client = self._client_factory()
        try:
            self._connect(client)
            return True
        finally:
            client.close()

    def run_service_status(self, service_name: str) -> SSHCommandResult:
        if (
            not SERVICE_PATTERN.fullmatch(service_name)
            or service_name not in self.host.allowed_services
        ):
            from app.infrastructure.errors import ServiceNotAllowedError

            raise ServiceNotAllowedError("Service is not allowed")
        command = (
            "systemctl show --no-pager "
            f"--property={_SERVICE_PROPERTIES} {service_name}"
        )
        return self._execute("service_status", command)

    def _connect(self, client: paramiko.SSHClient) -> None:
        try:
            client.load_host_keys(str(self.host.known_hosts_file))
            client.set_missing_host_key_policy(_RejectUnknownHostKey())
            client.connect(
                hostname=self.host.hostname,
                port=self.host.port,
                username=self.host.username,
                key_filename=str(self.host.identity_file),
                timeout=self.host.connect_timeout_seconds,
                banner_timeout=self.host.connect_timeout_seconds,
                auth_timeout=self.host.connect_timeout_seconds,
                allow_agent=False,
                look_for_keys=False,
                password=None,
            )
        except UnknownHostKeyError:
            raise
        except paramiko.BadHostKeyException as error:
            raise ChangedHostKeyError("SSH host key changed") from error
        except paramiko.AuthenticationException as error:
            raise AuthenticationFailedError("SSH authentication failed") from error
        except (OSError, socket.timeout, paramiko.SSHException) as error:
            raise ConnectionFailedError("SSH connection failed") from error

    def _execute(
        self, command_name: str, command: str, *, stdin_data: str | None = None
    ) -> SSHCommandResult:
        started_at = time.monotonic()
        client = self._client_factory()
        try:
            self._connect(client)
            stdin, stdout, stderr = client.exec_command(
                command,
                timeout=self.host.command_timeout_seconds,
                get_pty=False,
            )
            if stdin_data is not None:
                stdin.write(stdin_data)
                stdin.flush()
            stdin.channel.shutdown_write()
            stdout_bytes, stderr_bytes = self._read_bounded(stdout.channel)
            exit_code = stdout.channel.recv_exit_status()
        except (CommandTimeoutError, OutputTooLargeError):
            raise
        except socket.timeout as error:
            raise CommandTimeoutError("SSH command timed out") from error
        except paramiko.SSHException as error:
            raise ConnectionFailedError("SSH command transport failed") from error
        finally:
            client.close()

        return SSHCommandResult(
            host=self.host.alias,
            command_name=command_name,
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            duration_ms=round((time.monotonic() - started_at) * 1_000, 3),
            timed_out=False,
        )

    def _read_bounded(
        self, channel: paramiko.Channel
    ) -> tuple[bytes, bytes]:
        stdout = bytearray()
        stderr = bytearray()
        deadline = time.monotonic() + self.host.command_timeout_seconds
        while True:
            while channel.recv_ready():
                stdout.extend(channel.recv(32768))
                self._check_output_size(stdout, stderr)
            while channel.recv_stderr_ready():
                stderr.extend(channel.recv_stderr(32768))
                self._check_output_size(stdout, stderr)
            if channel.exit_status_ready():
                while channel.recv_ready():
                    stdout.extend(channel.recv(32768))
                    self._check_output_size(stdout, stderr)
                while channel.recv_stderr_ready():
                    stderr.extend(channel.recv_stderr(32768))
                    self._check_output_size(stdout, stderr)
                return bytes(stdout), bytes(stderr)
            if time.monotonic() >= deadline:
                channel.close()
                raise CommandTimeoutError("SSH command timed out")
            time.sleep(0.01)

    def _check_output_size(
        self, stdout: bytearray, stderr: bytearray
    ) -> None:
        if len(stdout) + len(stderr) > self.max_output_bytes:
            raise OutputTooLargeError("SSH command output exceeded the limit")
