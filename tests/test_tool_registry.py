"""Tests for ToolRegistry."""

from typing import Any

import pytest

from app.tools.base import Tool
from app.tools.registry import ToolRegistry


class ExampleTool(Tool):
    @property
    def name(self) -> str:
        return "example"

    @property
    def description(self) -> str:
        return "Example tool."

    def parameters(self) -> dict[str, Any]:
        return {}

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs


def test_registry_registers_gets_and_lists_tool() -> None:
    registry = ToolRegistry()
    tool = ExampleTool()

    registry.register(tool)

    assert registry.get("example") is tool
    assert registry.list_tools() == [tool]


def test_registry_rejects_duplicate_name() -> None:
    registry = ToolRegistry()
    registry.register(ExampleTool())

    with pytest.raises(ValueError, match="already registered"):
        registry.register(ExampleTool())


def test_registry_unregisters_tool() -> None:
    registry = ToolRegistry()
    tool = ExampleTool()
    registry.register(tool)

    assert registry.unregister("example") is tool

    with pytest.raises(KeyError, match="Unknown tool"):
        registry.get("example")
