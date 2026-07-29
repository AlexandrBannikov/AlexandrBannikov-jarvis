"""Safe execution facade for registered tools."""

import logging
import time
from typing import Any

from app.tools.registry import ToolRegistry
from app.tools.result import ToolResult
from app.infrastructure.errors import InfrastructureError

logger = logging.getLogger(__name__)


class ToolManager:
    """Execute tools with timing, logging, and exception isolation."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, name: str, **kwargs: Any) -> ToolResult:
        started_at = time.monotonic()
        logger.info("Tool execution started: tool=%s", name)
        try:
            tool = self.registry.get(name)
            data = tool.execute(**kwargs)
            if not isinstance(data, dict):
                raise TypeError("Tool execute() must return a dict")
        except InfrastructureError as error:
            duration_ms = round((time.monotonic() - started_at) * 1_000, 3)
            logger.warning(
                "Tool execution failed: tool=%s duration_ms=%.3f error_type=%s",
                name,
                duration_ms,
                error.code,
            )
            return ToolResult(
                success=False,
                tool=name,
                data={},
                message="Tool execution failed.",
                duration_ms=duration_ms,
                error=error.code,
            )
        except Exception as error:
            duration_ms = round(
                (time.monotonic() - started_at) * 1_000, 3
            )
            logger.exception(
                "Tool execution failed: tool=%s duration_ms=%.3f",
                name,
                duration_ms,
            )
            return ToolResult(
                success=False,
                tool=name,
                data={},
                message="Tool execution failed.",
                duration_ms=duration_ms,
                error=f"{type(error).__name__}: {error}",
            )

        duration_ms = round((time.monotonic() - started_at) * 1_000, 3)
        logger.info(
            "Tool execution finished: tool=%s success=true duration_ms=%.3f",
            name,
            duration_ms,
        )
        return ToolResult(
            success=True,
            tool=name,
            data=data,
            message="Tool executed successfully.",
            duration_ms=duration_ms,
            error=None,
        )
