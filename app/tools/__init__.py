"""Jarvis tool framework and built-in tools."""

from app.tools.base import Tool
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult
from app.tools.system_info import SystemInfoTool


def create_default_tool_manager() -> ToolManager:
    """Create a manager containing Jarvis built-in tools."""
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    return ToolManager(registry)


__all__ = [
    "SystemInfoTool",
    "Tool",
    "ToolManager",
    "ToolRegistry",
    "ToolResult",
    "create_default_tool_manager",
]
