"""Structured result returned for every tool execution."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ToolResult:
    success: bool
    tool: str
    data: dict[str, Any]
    message: str
    duration_ms: float
    error: str | None = None
