"""Tests for ToolResult."""

from dataclasses import asdict

from app.tools.result import ToolResult


def test_tool_result_contains_execution_metadata() -> None:
    result = ToolResult(
        success=True,
        tool="example",
        data={"value": 42},
        message="done",
        duration_ms=1.5,
        error=None,
    )

    assert asdict(result) == {
        "success": True,
        "tool": "example",
        "data": {"value": 42},
        "message": "done",
        "duration_ms": 1.5,
        "error": None,
    }
