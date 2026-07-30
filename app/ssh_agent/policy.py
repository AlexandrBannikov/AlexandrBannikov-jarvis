"""Authorization and deterministic argv construction for read-only operations."""

from __future__ import annotations

from .errors import ErrorCode, ExecutionPlanError, OperationPolicyError
from .execution_plan import (
    CompositeExecutionPlan,
    ExecutionPlan,
    ProjectListItem,
    ProjectListResult,
    ServerListItem,
    ServerListResult,
    validate_composite_execution_plan,
    validate_execution_plan,
)
from .models import ProjectConfig
from .operations import OPERATION_CATALOG, OperationName
from .registry import ServerRegistry

DEFAULT_JOURNAL_LINES = 50
MIN_JOURNAL_LINES = 1
MAX_JOURNAL_LINES = 200
MAX_PROJECT_SUMMARY_SERVICES = 32
MIN_PROCESS_LIMIT = 1
MAX_PROCESS_LIMIT = 30
PROCESS_SORTS = frozenset({"cpu", "memory"})

_STATUS_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "Result",
    "ExecMainStatus",
    "ActiveEnterTimestamp",
    "InactiveEnterTimestamp",
)


class CommandPolicy:
    def __init__(self, registry: ServerRegistry) -> None:
        self._registry = registry

    def build_plan(
        self,
        operation: str,
        server_alias: str | None = None,
        *,
        project_alias: str | None = None,
        service_name: str | None = None,
        lines: int | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> (
        ExecutionPlan
        | CompositeExecutionPlan
        | ServerListResult
        | ProjectListResult
    ):
        definition = OPERATION_CATALOG.get(operation)
        if definition is None:
            raise OperationPolicyError(ErrorCode.OPERATION_NOT_SUPPORTED)
        if definition.requires_server and server_alias is None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_REQUIRED)
        if not definition.requires_server and server_alias is not None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_FORBIDDEN)
        if definition.requires_project and project_alias is None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_REQUIRED)
        if not definition.requires_project and project_alias is not None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_FORBIDDEN)
        if definition.requires_service and service_name is None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_REQUIRED)
        if not definition.requires_service and service_name is not None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_FORBIDDEN)
        if not definition.accepts_lines and lines is not None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_FORBIDDEN)
        if definition.accepts_lines:
            lines = self._validate_lines(lines)
        if not definition.accepts_sort_by and sort_by is not None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_FORBIDDEN)
        if definition.accepts_sort_by:
            sort_by = self._validate_process_sort(sort_by)
        if not definition.accepts_limit and limit is not None:
            raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_FORBIDDEN)
        if definition.accepts_limit:
            limit = self._validate_process_limit(limit)

        if operation == OperationName.LIST_SERVERS:
            return self._list_servers()
        if operation == OperationName.LIST_PROJECTS:
            assert server_alias is not None
            return self._list_projects(server_alias)

        assert server_alias is not None
        server = self._registry.get_server(server_alias)
        project = (
            self._registry.get_project(server_alias, project_alias)
            if project_alias is not None
            else None
        )
        if service_name is not None:
            if project_alias is None:
                raise OperationPolicyError(ErrorCode.OPERATION_PARAMETER_REQUIRED)
            self._registry.service_allowed(
                server_alias, project_alias, service_name
            )

        if operation == OperationName.SERVER_SUMMARY:
            return self.build_server_summary(server.alias)
        if operation == OperationName.PROJECT_SUMMARY:
            assert project is not None
            return self.build_project_summary(server.alias, project.alias)

        plan = self._build_single(
            operation,
            server.alias,
            project=project,
            service_name=service_name,
            lines=lines,
            sort_by=sort_by,
            limit=limit,
        )
        self._validate_authorized_plan(plan, project)
        return plan

    def build_server_summary(self, server_alias: str) -> CompositeExecutionPlan:
        server = self._registry.get_server(server_alias)
        children = (
            self._fixed_plan(
                "hostname", server.alias, ("/bin/cat", "/etc/hostname"), 5, 8_192, 100
            ),
            self._build_single(OperationName.UPTIME, server.alias),
            self._build_single(OperationName.LOAD_AVERAGE, server.alias),
            self._build_single(OperationName.MEMORY_USAGE, server.alias),
            self._build_single(OperationName.DISK_USAGE, server.alias),
        )
        for child in children:
            validate_execution_plan(child)
        plan = CompositeExecutionPlan(
            OperationName.SERVER_SUMMARY, server.alias, children
        )
        validate_composite_execution_plan(plan)
        return plan

    def build_project_summary(
        self, server_alias: str, project_alias: str
    ) -> CompositeExecutionPlan:
        server = self._registry.get_server(server_alias)
        project = self._registry.get_project(server.alias, project_alias)
        if len(project.services) > MAX_PROJECT_SUMMARY_SERVICES:
            raise ExecutionPlanError()
        children = [
            self._build_single(
                OperationName.PROJECT_GIT_STATUS, server.alias, project=project
            ),
            self._build_single(
                OperationName.PROJECT_LAST_COMMIT, server.alias, project=project
            ),
        ]
        children.extend(
            self._build_single(
                OperationName.SERVICE_STATUS,
                server.alias,
                project=project,
                service_name=service,
            )
            for service in project.services
        )
        for child in children:
            self._validate_authorized_plan(child, project)
        plan = CompositeExecutionPlan(
            OperationName.PROJECT_SUMMARY,
            server.alias,
            tuple(children),
            {"project": project.alias, "service_count": len(project.services)},
        )
        validate_composite_execution_plan(plan)
        return plan

    @staticmethod
    def _validate_lines(lines: int | None) -> int:
        if lines is None:
            return DEFAULT_JOURNAL_LINES
        if (
            isinstance(lines, bool)
            or not isinstance(lines, int)
            or not MIN_JOURNAL_LINES <= lines <= MAX_JOURNAL_LINES
        ):
            raise OperationPolicyError(ErrorCode.INVALID_LINE_LIMIT)
        return lines

    @staticmethod
    def _validate_process_sort(sort_by: str | None) -> str:
        if sort_by not in PROCESS_SORTS:
            raise OperationPolicyError(ErrorCode.INVALID_PROCESS_SORT)
        return sort_by

    @staticmethod
    def _validate_process_limit(limit: int | None) -> int:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not MIN_PROCESS_LIMIT <= limit <= MAX_PROCESS_LIMIT
        ):
            raise OperationPolicyError(ErrorCode.INVALID_PROCESS_LIMIT)
        return limit

    def _list_servers(self) -> ServerListResult:
        return ServerListResult(
            tuple(
                ServerListItem(server.alias, server.enabled, len(server.projects))
                for server in self._registry.list_servers(include_disabled=True)
            )
        )

    def _list_projects(self, server_alias: str) -> ProjectListResult:
        server = self._registry.get_server(server_alias, require_enabled=False)
        return ProjectListResult(
            server.alias,
            server.enabled,
            tuple(
                ProjectListItem(project.alias, project.services)
                for project in self._registry.list_projects(
                    server.alias, include_disabled_server=True
                )
            ),
        )

    def _build_single(
        self,
        operation: str,
        server_alias: str,
        *,
        project: ProjectConfig | None = None,
        service_name: str | None = None,
        lines: int | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
    ) -> ExecutionPlan:
        if operation == OperationName.DISK_USAGE:
            return self._fixed_plan(
                operation, server_alias, ("/bin/df", "-P", "-B1", "/"), 10, 64_000, 500
            )
        if operation == OperationName.MEMORY_USAGE:
            return self._fixed_plan(
                operation, server_alias, ("/bin/cat", "/proc/meminfo"), 5, 64_000, 500
            )
        if operation == OperationName.LOAD_AVERAGE:
            return self._fixed_plan(
                operation, server_alias, ("/bin/cat", "/proc/loadavg"), 5, 8_192, 50
            )
        if operation == OperationName.UPTIME:
            return self._fixed_plan(
                operation, server_alias, ("/bin/cat", "/proc/uptime"), 5, 8_192, 50
            )
        if operation == OperationName.SERVICE_STATUS:
            assert service_name is not None
            properties = ",".join(_STATUS_PROPERTIES)
            return self._fixed_plan(
                operation,
                server_alias,
                (
                    "/usr/bin/systemctl",
                    "show",
                    "--no-pager",
                    f"--property={properties}",
                    "--",
                    service_name,
                ),
                15,
                128_000,
                500,
                metadata={"service": service_name},
            )
        if operation == OperationName.SERVICE_RECENT_LOGS:
            assert service_name is not None and lines is not None
            return self._fixed_plan(
                operation,
                server_alias,
                (
                    "/usr/bin/journalctl",
                    "--no-pager",
                    "--output=short-iso",
                    "--lines",
                    str(lines),
                    "--unit",
                    service_name,
                ),
                20,
                256_000,
                lines,
                sensitive_output=True,
                metadata={"service": service_name, "lines": lines},
            )
        if operation == OperationName.TOP_PROCESSES:
            assert sort_by is not None and limit is not None
            sort_field = "%cpu" if sort_by == "cpu" else "%mem"
            return self._fixed_plan(
                operation,
                server_alias,
                (
                    "/usr/bin/ps",
                    "-eo",
                    "pid=,user=,%cpu=,%mem=,etime=,comm=",
                    f"--sort=-{sort_field}",
                ),
                10,
                256_000,
                1_000,
                metadata={"sort_by": sort_by, "limit": limit},
            )
        if operation == OperationName.PROJECT_GIT_STATUS:
            assert project is not None
            return self._fixed_plan(
                operation,
                server_alias,
                (
                    "/usr/bin/git",
                    "--no-pager",
                    "-c",
                    "credential.interactive=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-C",
                    str(project.path),
                    "status",
                    "--short",
                    "--branch",
                ),
                15,
                128_000,
                1_000,
                metadata={"project": project.alias},
            )
        if operation == OperationName.PROJECT_LAST_COMMIT:
            assert project is not None
            return self._fixed_plan(
                operation,
                server_alias,
                (
                    "/usr/bin/git",
                    "--no-pager",
                    "-c",
                    "credential.interactive=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-C",
                    str(project.path),
                    "log",
                    "-1",
                    "--no-decorate",
                    "--format=%h%x00%s%x00%aI",
                ),
                15,
                128_000,
                100,
                metadata={"project": project.alias},
            )
        raise OperationPolicyError(ErrorCode.OPERATION_NOT_SUPPORTED)

    @staticmethod
    def _fixed_plan(
        operation: str,
        server_alias: str,
        argv: tuple[str, ...],
        timeout_seconds: int,
        stdout_limit_bytes: int,
        max_output_lines: int,
        *,
        sensitive_output: bool = False,
        metadata: dict[str, str | int | bool] | None = None,
    ) -> ExecutionPlan:
        return ExecutionPlan(
            str(operation),
            server_alias,
            argv,
            timeout_seconds,
            stdout_limit_bytes,
            16_384,
            max_output_lines,
            sensitive_output,
            metadata or {},
        )

    @staticmethod
    def _validate_authorized_plan(
        plan: ExecutionPlan, project: ProjectConfig | None
    ) -> None:
        paths = frozenset({str(project.path)}) if project is not None else frozenset()
        services = frozenset(project.services) if project is not None else frozenset()
        validate_execution_plan(
            plan, trusted_project_paths=paths, trusted_services=services
        )
