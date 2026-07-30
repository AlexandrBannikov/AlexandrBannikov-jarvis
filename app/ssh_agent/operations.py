"""Fixed read-only operation catalog and parameter declarations."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from collections.abc import Mapping


class OperationName(StrEnum):
    SERVER_SUMMARY = "server_summary"
    DISK_USAGE = "disk_usage"
    MEMORY_USAGE = "memory_usage"
    LOAD_AVERAGE = "load_average"
    UPTIME = "uptime"
    SERVICE_STATUS = "service_status"
    SERVICE_RECENT_LOGS = "service_recent_logs"
    PROJECT_GIT_STATUS = "project_git_status"
    PROJECT_LAST_COMMIT = "project_last_commit"
    PROJECT_SUMMARY = "project_summary"
    LIST_SERVERS = "list_servers"
    LIST_PROJECTS = "list_projects"


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    name: OperationName
    remote: bool
    requires_server: bool = True
    requires_project: bool = False
    requires_service: bool = False
    accepts_lines: bool = False
    composite: bool = False


OPERATION_CATALOG: Mapping[str, OperationDefinition] = MappingProxyType(
    {
        definition.name.value: definition
        for definition in (
            OperationDefinition(OperationName.SERVER_SUMMARY, True, composite=True),
            OperationDefinition(OperationName.DISK_USAGE, True),
            OperationDefinition(OperationName.MEMORY_USAGE, True),
            OperationDefinition(OperationName.LOAD_AVERAGE, True),
            OperationDefinition(OperationName.UPTIME, True),
            OperationDefinition(
                OperationName.SERVICE_STATUS,
                True,
                requires_project=True,
                requires_service=True,
            ),
            OperationDefinition(
                OperationName.SERVICE_RECENT_LOGS,
                True,
                requires_project=True,
                requires_service=True,
                accepts_lines=True,
            ),
            OperationDefinition(
                OperationName.PROJECT_GIT_STATUS, True, requires_project=True
            ),
            OperationDefinition(
                OperationName.PROJECT_LAST_COMMIT, True, requires_project=True
            ),
            OperationDefinition(
                OperationName.PROJECT_SUMMARY,
                True,
                requires_project=True,
                composite=True,
            ),
            OperationDefinition(
                OperationName.LIST_SERVERS, False, requires_server=False
            ),
            OperationDefinition(
                OperationName.LIST_PROJECTS, False, requires_project=False
            ),
        )
    }
)

SUPPORTED_OPERATION_NAMES = frozenset(OPERATION_CATALOG)
