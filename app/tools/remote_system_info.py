"""Read-only remote system information tool."""

import json
import logging
import time
from typing import Any, Callable

from app.infrastructure.errors import CommandFailedError
from app.infrastructure.hosts import HostsConfig
from app.infrastructure.ssh_client import SSHClient
from app.tools.base import Tool

audit_logger = logging.getLogger("jarvis.audit")
_EXPECTED_FIELDS = frozenset(
    {
        "hostname",
        "kernel",
        "os",
        "architecture",
        "uptime_seconds",
        "cpu_count",
        "memory_total_bytes",
        "memory_available_bytes",
        "disk_total_bytes",
        "disk_available_bytes",
        "current_utc_time",
    }
)


class RemoteSystemInfoTool(Tool):
    def __init__(
        self,
        hosts: HostsConfig,
        client_factory: Callable[..., SSHClient] = SSHClient,
    ) -> None:
        self.hosts = hosts
        self.client_factory = client_factory

    @property
    def name(self) -> str:
        return "remote_system_info"

    @property
    def description(self) -> str:
        return (
            "Returns read-only system information for a configured remote host."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"host_alias": {"type": "string"}},
            "required": ["host_alias"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        alias = kwargs.get("host_alias")
        initiator = kwargs.get("initiator_user_id")
        if not isinstance(alias, str):
            from app.infrastructure.errors import UnknownHostError

            raise UnknownHostError("Remote host is not configured")
        host = self.hosts.get(alias)
        started_at = time.monotonic()
        audit_logger.info(
            "remote_operation_started user_id=%s tool=%s host=%s",
            initiator,
            self.name,
            alias,
        )
        try:
            result = self.client_factory(host).run_system_info()
            if result.exit_code != 0:
                raise CommandFailedError("Remote diagnostic command failed")
            try:
                raw_data = json.loads(result.stdout)
            except (json.JSONDecodeError, UnicodeError) as error:
                raise CommandFailedError(
                    "Remote diagnostic response is invalid"
                ) from error
            if (
                not isinstance(raw_data, dict)
                or not _EXPECTED_FIELDS.issubset(raw_data)
            ):
                raise CommandFailedError("Remote diagnostic response is invalid")
            data = {name: raw_data[name] for name in _EXPECTED_FIELDS}
        except Exception as error:
            audit_logger.info(
                "remote_operation_finished user_id=%s tool=%s host=%s "
                "success=false duration_ms=%.3f error_type=%s",
                initiator,
                self.name,
                alias,
                (time.monotonic() - started_at) * 1_000,
                getattr(error, "code", type(error).__name__),
            )
            raise
        audit_logger.info(
            "remote_operation_finished user_id=%s tool=%s host=%s "
            "success=true duration_ms=%.3f error_type=none",
            initiator,
            self.name,
            alias,
            (time.monotonic() - started_at) * 1_000,
        )
        return {"host": alias, **data}
