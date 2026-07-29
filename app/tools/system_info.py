"""Read-only tool providing basic host and process information."""

from datetime import datetime, timezone
import os
import platform
import socket
import time
from typing import Any

from app.tools.base import Tool

PROCESS_STARTED_AT = time.monotonic()


class SystemInfoTool(Tool):
    """Return basic information about the current Jarvis host."""

    @property
    def name(self) -> str:
        return "system_info"

    @property
    def description(self) -> str:
        return "Returns basic information about current host."

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        if kwargs:
            raise ValueError("system_info does not accept parameters")
        return {
            "hostname": socket.gethostname(),
            "os": platform.platform(),
            "python_version": platform.python_version(),
            "cpu_count": os.cpu_count(),
            "architecture": platform.machine(),
            "current_utc_time": datetime.now(timezone.utc).isoformat(
                timespec="seconds"
            ),
            "uptime_seconds": round(time.monotonic() - PROCESS_STARTED_AT, 3),
            "pid": os.getpid(),
        }
