"""Tests for ToolManager."""

from unittest.mock import Mock

from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


def make_manager(tool: Mock) -> ToolManager:
    registry = ToolRegistry()
    registry.register(tool)
    return ToolManager(registry)


def test_manager_executes_tool_and_returns_result() -> None:
    tool = Mock()
    tool.name = "example"
    tool.execute.return_value = {"answer": 42}
    manager = make_manager(tool)

    result = manager.execute("example", question="test")

    assert result.success is True
    assert result.tool == "example"
    assert result.data == {"answer": 42}
    assert result.error is None
    assert result.duration_ms >= 0
    tool.execute.assert_called_once_with(question="test")


def test_manager_catches_tool_exception() -> None:
    tool = Mock()
    tool.name = "broken"
    tool.execute.side_effect = RuntimeError("failure")
    manager = make_manager(tool)

    result = manager.execute("broken")

    assert result.success is False
    assert result.data == {}
    assert result.message == "Tool execution failed."
    assert result.error == "RuntimeError: failure"


def test_manager_handles_unknown_tool() -> None:
    result = ToolManager(ToolRegistry()).execute("missing")

    assert result.success is False
    assert result.tool == "missing"
    assert result.error is not None
    assert "Unknown tool" in result.error


def test_manager_rejects_non_dict_result() -> None:
    tool = Mock()
    tool.name = "invalid"
    tool.execute.return_value = "not-a-dict"
    manager = make_manager(tool)

    result = manager.execute("invalid")

    assert result.success is False
    assert result.error is not None
    assert "must return a dict" in result.error
