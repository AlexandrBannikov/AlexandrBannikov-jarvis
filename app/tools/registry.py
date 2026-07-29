"""Tool registration and discovery."""

from app.tools.base import Tool


class ToolRegistry:
    """Maintain a unique set of tools indexed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool name must not be empty")
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> Tool:
        try:
            return self._tools.pop(name)
        except KeyError as error:
            raise KeyError(f"Unknown tool: {name}") from error

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as error:
            raise KeyError(f"Unknown tool: {name}") from error

    def list_tools(self) -> list[Tool]:
        return [self._tools[name] for name in sorted(self._tools)]
