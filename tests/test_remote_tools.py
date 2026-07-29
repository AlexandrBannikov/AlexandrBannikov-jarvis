"""Tests for read-only remote tools and sanitized ToolResult errors."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.infrastructure.errors import UnknownHostKeyError
from app.infrastructure.hosts import HostConfig, HostsConfig
from app.infrastructure.ssh_client import SSHCommandResult
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.tools.remote_service_status import RemoteServiceStatusTool
from app.tools.remote_system_info import RemoteSystemInfoTool


def configured_hosts() -> HostsConfig:
    return HostsConfig(
        {
            "crypto": HostConfig(
                alias="crypto",
                hostname="example.test",
                port=22,
                username="jarvis-monitor",
                identity_file=Path("/key"),
                known_hosts_file=Path("/known_hosts"),
                connect_timeout_seconds=10,
                command_timeout_seconds=15,
                allowed_services=frozenset({"safe.service"}),
            )
        }
    )


def result(stdout: str, command: str) -> SSHCommandResult:
    return SSHCommandResult(
        host="crypto",
        command_name=command,
        exit_code=0,
        stdout=stdout,
        stderr="",
        duration_ms=1,
        timed_out=False,
    )


def test_remote_system_info_returns_structured_data() -> None:
    client = Mock()
    client.run_system_info.return_value = result(
        json.dumps(
            {
                "hostname": "remote",
                "kernel": "test",
                "os": "Test OS",
                "architecture": "x86_64",
                "uptime_seconds": 1,
                "cpu_count": 2,
                "memory_total_bytes": 100,
                "memory_available_bytes": 50,
                "disk_total_bytes": 200,
                "disk_available_bytes": 75,
                "current_utc_time": "2026-01-01T00:00:00+00:00",
                "unexpected": "not returned",
            }
        ),
        "system_info",
    )
    tool = RemoteSystemInfoTool(
        configured_hosts(), client_factory=lambda host: client
    )

    data = tool.execute(host_alias="crypto")

    assert data["host"] == "crypto"
    assert data["hostname"] == "remote"
    assert data["cpu_count"] == 2
    assert "unexpected" not in data


def test_remote_service_status_returns_structured_data() -> None:
    client = Mock()
    client.run_service_status.return_value = result(
        "Id=safe.service\nDescription=Safe\nLoadState=loaded\n"
        "ActiveState=active\nSubState=running\nUnitFileState=enabled\n"
        "MainPID=42\nExecMainStatus=0\n"
        "ActiveEnterTimestamp=now\nInactiveEnterTimestamp=never\n"
        "Unexpected=hidden\n",
        "service_status",
    )
    tool = RemoteServiceStatusTool(
        configured_hosts(), client_factory=lambda host: client
    )

    data = tool.execute(host_alias="crypto", service_name="safe.service")

    assert data["ActiveState"] == "active"
    assert data["MainPID"] == 42
    assert "Unexpected" not in data
    client.run_service_status.assert_called_once_with("safe.service")


def test_unknown_alias_is_sanitized_by_manager() -> None:
    registry = ToolRegistry()
    registry.register(RemoteSystemInfoTool(configured_hosts()))

    output = ToolManager(registry).execute(
        "remote_system_info", host_alias="missing"
    )

    assert output.success is False
    assert output.error == "unknown_host"


def test_infrastructure_error_does_not_expose_details() -> None:
    client = Mock()
    client.run_system_info.side_effect = UnknownHostKeyError(
        "/private/key secret detail"
    )
    registry = ToolRegistry()
    registry.register(
        RemoteSystemInfoTool(
            configured_hosts(), client_factory=lambda host: client
        )
    )

    output = ToolManager(registry).execute(
        "remote_system_info", host_alias="crypto"
    )

    assert output.error == "unknown_host_key"
    assert "/private" not in str(output)
