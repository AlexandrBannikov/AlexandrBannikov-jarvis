"""Tests for strict Responses API tool schemas and local validation."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.ai.tool_adapter import (
    ToolAdapter,
    ToolCallValidationError,
    UnknownToolCallError,
    serialize_tool_result,
)
from app.infrastructure.hosts import HostConfig, HostsConfig
from app.memory.tools import register_memory_tools
from app.reminders.tools import register_reminder_tools
from app.ssh_agent.tools import register_ssh_tools
from app.tools.registry import ToolRegistry
from app.tools.remote_service_status import RemoteServiceStatusTool
from app.tools.remote_system_info import RemoteSystemInfoTool
from app.tools.result import ToolResult
from app.tools.system_info import SystemInfoTool


def adapter() -> ToolAdapter:
    hosts = HostsConfig(
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
                allowed_services=frozenset({"crypto-paper.timer"}),
            )
        }
    )
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    registry.register(RemoteSystemInfoTool(hosts))
    registry.register(RemoteServiceStatusTool(hosts))
    return ToolAdapter(registry)


def schema(name: str) -> dict:
    return next(item for item in adapter().schemas() if item["name"] == name)


def test_all_tools_are_strict_responses_api_functions() -> None:
    schemas = adapter().schemas()

    assert [item["name"] for item in schemas] == [
        "remote_service_status",
        "remote_system_info",
        "system_info",
    ]
    for item in schemas:
        assert item["type"] == "function"
        assert item["strict"] is True
        assert item["parameters"]["type"] == "object"
        assert item["parameters"]["additionalProperties"] is False
        assert isinstance(item["parameters"]["required"], list)


def test_all_production_function_tools_have_complete_strict_schemas() -> None:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    register_memory_tools(registry, Mock())
    register_ssh_tools(registry, Mock())
    register_reminder_tools(registry, Mock())

    schemas = ToolAdapter(registry).schemas()

    assert {
        "system_info",
        "remember",
        "list_ssh_servers",
        "get_server_summary",
        "get_project_summary",
        "get_service_recent_logs",
        "create_reminder",
    } <= {item["name"] for item in schemas}

    def assert_strict_object(schema: dict) -> None:
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert isinstance(schema["properties"], dict)
        assert set(schema["required"]) == set(schema["properties"])
        for child in schema["properties"].values():
            if child.get("type") == "object":
                assert_strict_object(child)

    for item in schemas:
        assert item["type"] == "function"
        assert item["strict"] is True
        assert_strict_object(item["parameters"])


def test_system_info_schema_has_no_arguments() -> None:
    parameters = schema("system_info")["parameters"]

    assert parameters["properties"] == {}
    assert parameters["required"] == []


def test_remote_system_info_schema_has_only_host_alias() -> None:
    parameters = schema("remote_system_info")["parameters"]

    assert set(parameters["properties"]) == {"host_alias"}
    assert parameters["required"] == ["host_alias"]


def test_remote_service_schema_has_no_command_argument() -> None:
    parameters = schema("remote_service_status")["parameters"]

    assert set(parameters["properties"]) == {"host_alias", "service_name"}
    assert parameters["required"] == ["host_alias", "service_name"]
    assert "command" not in json.dumps(parameters).lower()


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("missing", "{}", "unknown_tool"),
        ("system_info", "{broken", "invalid_tool_arguments"),
        ("system_info", "[]", "invalid_tool_arguments"),
        (
            "remote_system_info",
            '{"host_alias":"crypto","extra":"x"}',
            "invalid_tool_arguments",
        ),
        ("remote_system_info", "{}", "invalid_tool_arguments"),
        (
            "remote_system_info",
            '{"host_alias":123}',
            "invalid_tool_arguments",
        ),
        (
            "remote_system_info",
            '{"host_alias":"missing"}',
            "unknown_host",
        ),
        (
            "remote_service_status",
            '{"host_alias":"crypto","service_name":"other.service"}',
            "service_not_allowed",
        ),
        (
            "remote_service_status",
            '{"host_alias":"crypto","service_name":"x;id"}',
            "service_not_allowed",
        ),
    ],
)
def test_model_arguments_are_revalidated_locally(
    name: str, arguments: str, code: str
) -> None:
    error_type = UnknownToolCallError if code == "unknown_tool" else ToolCallValidationError
    with pytest.raises(error_type) as caught:
        adapter().parse_and_validate(name, arguments)

    assert caught.value.code == code


def test_safe_tool_result_serialization_omits_duration() -> None:
    output = serialize_tool_result(
        ToolResult(True, "system_info", {"ok": True}, "done", 123, None)
    )

    assert json.loads(output) == {
        "success": True,
        "tool": "system_info",
        "data": {"ok": True},
        "message": "done",
        "error": None,
    }
    assert "duration" not in output


def test_oversized_tool_output_is_replaced_safely() -> None:
    output = serialize_tool_result(
        ToolResult(True, "system_info", {"value": "x" * 100}, "done", 1),
        max_bytes=20,
    )

    assert json.loads(output)["error"] == "tool_output_too_large"
    assert "x" * 20 not in output
