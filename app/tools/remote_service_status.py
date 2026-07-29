"""Read-only remote systemd service status tool."""

import logging
import time
from typing import Any, Callable

from app.infrastructure.errors import (
    CommandFailedError,
    ServiceNotAllowedError,
)
from app.infrastructure.hosts import HostsConfig, SERVICE_PATTERN
from app.infrastructure.ssh_client import SSHClient
from app.tools.base import Tool

audit_logger = logging.getLogger("jarvis.audit")
_INTEGER_PROPERTIES = frozenset({"MainPID", "ExecMainStatus"})
_EXPECTED_PROPERTIES = frozenset(
    {
        "Id",
        "Description",
        "LoadState",
        "ActiveState",
        "SubState",
        "UnitFileState",
        "MainPID",
        "ExecMainStatus",
        "ActiveEnterTimestamp",
        "InactiveEnterTimestamp",
    }
)


class RemoteServiceStatusTool(Tool):
    def __init__(
        self,
        hosts: HostsConfig,
        client_factory: Callable[..., SSHClient] = SSHClient,
    ) -> None:
        self.hosts = hosts
        self.client_factory = client_factory

    @property
    def name(self) -> str:
        return "remote_service_status"

    @property
    def description(self) -> str:
        return (
            "Returns read-only systemd service status for an allowed service "
            "on a configured remote host."
        )

    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "host_alias": {"type": "string"},
                "service_name": {"type": "string"},
            },
            "required": ["host_alias", "service_name"],
            "additionalProperties": False,
        }

    def execute(self, **kwargs: Any) -> dict[str, Any]:
        alias = kwargs.get("host_alias")
        service = kwargs.get("service_name")
        initiator = kwargs.get("initiator_user_id")
        if not isinstance(alias, str):
            from app.infrastructure.errors import UnknownHostError

            raise UnknownHostError("Remote host is not configured")
        host = self.hosts.get(alias)
        if (
            not isinstance(service, str)
            or not SERVICE_PATTERN.fullmatch(service)
            or service not in host.allowed_services
        ):
            raise ServiceNotAllowedError("Service is not allowed")

        started_at = time.monotonic()
        audit_logger.info(
            "remote_operation_started user_id=%s tool=%s host=%s service=%s",
            initiator,
            self.name,
            alias,
            service,
        )
        try:
            result = self.client_factory(host).run_service_status(service)
            if result.exit_code != 0:
                raise CommandFailedError("Remote diagnostic command failed")
            data = self._parse_properties(result.stdout)
        except Exception as error:
            audit_logger.info(
                "remote_operation_finished user_id=%s tool=%s host=%s "
                "service=%s success=false duration_ms=%.3f error_type=%s",
                initiator,
                self.name,
                alias,
                service,
                (time.monotonic() - started_at) * 1_000,
                getattr(error, "code", type(error).__name__),
            )
            raise
        audit_logger.info(
            "remote_operation_finished user_id=%s tool=%s host=%s service=%s "
            "success=true duration_ms=%.3f error_type=none",
            initiator,
            self.name,
            alias,
            service,
            (time.monotonic() - started_at) * 1_000,
        )
        return {"host": alias, "service": service, **data}

    @staticmethod
    def _parse_properties(output: str) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for line in output.splitlines():
            if "=" not in line:
                continue
            name, value = line.split("=", 1)
            if name not in _EXPECTED_PROPERTIES:
                continue
            properties[name] = (
                int(value) if name in _INTEGER_PROPERTIES and value.isdigit()
                else value
            )
        if not _EXPECTED_PROPERTIES.issubset(properties):
            raise CommandFailedError("Remote service response is invalid")
        return properties
