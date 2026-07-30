"""Strict model tools backed only by the asynchronous SSH service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any
import re

from app.tools.base import Tool
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult

from .service import SSHService
from .service_models import SSHRequestContext, SSHServiceResult
from .redaction import redact_secrets


def _schema(properties: dict[str, dict[str, Any]], required: list[str]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required,
            "additionalProperties": False}


def _safe_value(value: object) -> object:
    if isinstance(value, Enum):
        return _safe_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _safe_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {_safe_key(key): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe_value(item) for item in value]
    if isinstance(value, Path):
        return redact_secrets(str(value))[:256]
    if isinstance(value, str):
        return redact_secrets(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return "[UNSUPPORTED]"


def _safe_key(value: object) -> str:
    safe = _safe_value(value)
    if safe is None or isinstance(safe, (str, int, float, bool)):
        return str(safe)
    return "[UNSUPPORTED]"


class SSHServiceTool(Tool):
    """A schema-visible adapter; trusted execution is async and context-only."""

    service_method = ""
    operation = ""

    def __init__(self, service: SSHService) -> None:
        self.service = service

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise RuntimeError("SSH tools require trusted asynchronous context")

    def validate_arguments(self, arguments: dict[str, Any]) -> None:
        for name, value in arguments.items():
            if name == "lines":
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 200:
                    raise ValueError("invalid line limit")
            elif name == "limit":
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 30:
                    raise ValueError("invalid process limit")
            elif name == "sort_by":
                if value not in {"cpu", "memory"}:
                    raise ValueError("invalid process sort")
            elif (
                not isinstance(value, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}", value) is None
            ):
                raise ValueError("invalid alias")

    async def execute_trusted(
        self, context: SSHRequestContext, arguments: dict[str, Any],
    ) -> ToolResult:
        method = getattr(self.service, self.service_method)
        result: SSHServiceResult = await method(context, **arguments)
        message = self.service.format(result)
        data = {
            "success": result.success,
            "operation": result.operation,
            "server_alias": result.server_alias,
            "project_alias": result.project_alias,
            "service_name": result.service_name,
            "data": _safe_value(result.data),
            "formatted_message": message,
            "error_code": result.error_code.value if result.error_code else None,
            "truncated": result.truncated,
            "partial": result.partial,
        }
        return ToolResult(
            success=result.success,
            tool=self.name,
            data=data,
            message=message,
            duration_ms=result.duration_ms,
            error=result.error_code.value if result.error_code else None,
        )


class NoArgumentsTool(SSHServiceTool):
    def parameters(self) -> dict[str, Any]:
        return _schema({}, [])


class ServerTool(SSHServiceTool):
    def parameters(self) -> dict[str, Any]:
        return _schema({"server_alias": {"type": "string"}}, ["server_alias"])


class ProjectTool(ServerTool):
    def parameters(self) -> dict[str, Any]:
        return _schema(
            {"server_alias": {"type": "string"}, "project_alias": {"type": "string"}},
            ["server_alias", "project_alias"],
        )


class ListSSHServersTool(NoArgumentsTool):
    name = "list_ssh_servers"
    description = "List configured SSH server aliases. Read-only; never guess aliases."
    service_method = "list_servers"


class ListServerProjectsTool(ServerTool):
    name = "list_server_projects"
    description = "List configured project aliases on one selected server."
    service_method = "list_projects"


class GetServerSummaryTool(ServerTool):
    name = "get_server_summary"
    description = "Get a read-only current resource and uptime summary for one server."
    service_method = "get_server_summary"


class GetServerDiskUsageTool(ServerTool):
    name = "get_server_disk_usage"
    description = "Get current root filesystem usage for one configured server."
    service_method = "get_disk_usage"


class GetServerMemoryUsageTool(ServerTool):
    name = "get_server_memory_usage"
    description = "Get current memory usage for one configured server."
    service_method = "get_memory_usage"


class GetServerUptimeTool(ServerTool):
    name = "get_server_uptime"
    description = "Get current uptime for one configured server."
    service_method = "get_uptime"


class GetTopProcessesTool(SSHServiceTool):
    name = "get_top_processes"
    description = (
        "List a bounded set of current processes using the most CPU or memory "
        "on one configured server. Returns comm only, never command arguments."
    )
    service_method = "get_top_processes"

    def parameters(self) -> dict[str, Any]:
        properties = {
            "server_alias": {"type": "string"},
            "sort_by": {"type": "string", "enum": ["cpu", "memory"]},
            "limit": {"type": "integer", "minimum": 1, "maximum": 30},
        }
        return _schema(properties, list(properties))


class GetServiceStatusTool(SSHServiceTool):
    name = "get_service_status"
    description = "Get read-only status of one allowlisted service in a configured project."
    service_method = "get_service_status"

    def parameters(self) -> dict[str, Any]:
        properties = {
            "server_alias": {"type": "string"},
            "project_alias": {"type": "string"},
            "service_name": {"type": "string"},
        }
        return _schema(properties, list(properties))


class GetServiceRecentLogsTool(GetServiceStatusTool):
    name = "get_service_recent_logs"
    description = "Get bounded redacted recent logs for one allowlisted service."
    service_method = "get_service_recent_logs"

    def parameters(self) -> dict[str, Any]:
        properties = dict(super().parameters()["properties"])
        properties["lines"] = {"type": "integer", "minimum": 1, "maximum": 200}
        return _schema(
            properties,
            ["server_alias", "project_alias", "service_name", "lines"],
        )


class GetProjectStatusTool(ProjectTool):
    name = "get_project_status"
    description = "Get current read-only Git status for one configured project."
    service_method = "get_project_status"


class GetProjectLastCommitTool(ProjectTool):
    name = "get_project_last_commit"
    description = "Get the latest commit metadata for one configured project."
    service_method = "get_project_last_commit"


class GetProjectSummaryTool(ProjectTool):
    name = "get_project_summary"
    description = "Get current Git and allowlisted service status for one project."
    service_method = "get_project_summary"


SSH_TOOL_TYPES = (
    ListSSHServersTool, ListServerProjectsTool, GetServerSummaryTool,
    GetServerDiskUsageTool, GetServerMemoryUsageTool, GetServerUptimeTool,
    GetTopProcessesTool,
    GetServiceStatusTool, GetServiceRecentLogsTool, GetProjectStatusTool,
    GetProjectLastCommitTool, GetProjectSummaryTool,
)
SSH_TOOL_NAMES = frozenset(tool.name for tool in SSH_TOOL_TYPES)


def register_ssh_tools(registry: ToolRegistry, service: SSHService) -> None:
    for tool_type in SSH_TOOL_TYPES:
        registry.register(tool_type(service))
