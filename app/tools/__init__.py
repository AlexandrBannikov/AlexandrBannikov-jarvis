"""Jarvis tool framework and built-in tools."""

from app.tools.base import Tool
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult
from app.tools.system_info import SystemInfoTool
from app.infrastructure.hosts import DEFAULT_HOSTS_CONFIG, load_hosts_config
from app.tools.remote_service_status import RemoteServiceStatusTool
from app.tools.remote_system_info import RemoteSystemInfoTool


def create_default_tool_manager(
    hosts_config_path: str | None = None,
    *,
    include_legacy_remote: bool = True,
) -> ToolManager:
    """Create built-ins; production disables legacy pre-SSH-Agent tools."""
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    if include_legacy_remote:
        hosts = load_hosts_config(hosts_config_path or DEFAULT_HOSTS_CONFIG)
        registry.register(RemoteSystemInfoTool(hosts))
        registry.register(RemoteServiceStatusTool(hosts))
    return ToolManager(registry)


__all__ = [
    "SystemInfoTool",
    "RemoteSystemInfoTool",
    "RemoteServiceStatusTool",
    "Tool",
    "ToolManager",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_manager",
]
