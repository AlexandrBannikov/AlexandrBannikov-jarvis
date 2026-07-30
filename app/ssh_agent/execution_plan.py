"""Immutable execution plans and independent final safety validation."""

from dataclasses import dataclass
import re
from types import MappingProxyType
from collections.abc import Mapping

from .errors import ExecutionPlanError

ALLOWED_EXECUTABLES = frozenset(
    {
        "/bin/cat",
        "/bin/df",
        "/usr/bin/git",
        "/usr/bin/journalctl",
        "/usr/bin/ps",
        "/usr/bin/systemctl",
    }
)
ALLOWED_PLAN_OPERATIONS = frozenset(
    {
        "hostname",
        "disk_usage",
        "memory_usage",
        "load_average",
        "uptime",
        "service_status",
        "service_recent_logs",
        "project_git_status",
        "project_last_commit",
        "top_processes",
    }
)
MAX_ARGV_COUNT = 32
MAX_ARGUMENT_LENGTH = 4096
MAX_TIMEOUT_SECONDS = 30
MAX_STDOUT_LIMIT_BYTES = 512 * 1024
MAX_STDERR_LIMIT_BYTES = 64 * 1024
MAX_OUTPUT_LINES = 1_000
MAX_COMPOSITE_CHILDREN = 34
MAX_COMPOSITE_TIMEOUT_SECONDS = 600
MAX_COMPOSITE_OUTPUT_BYTES = 6 * 1024 * 1024
_ALIAS_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_SHELL_METACHARACTERS = frozenset(";|&`<>")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    operation: str
    server_alias: str
    argv: tuple[str, ...]
    timeout_seconds: int
    stdout_limit_bytes: int
    stderr_limit_bytes: int
    max_output_lines: int
    sensitive_output: bool = False
    metadata: Mapping[str, str | int | bool] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class CompositeExecutionPlan:
    operation: str
    server_alias: str
    children: tuple[ExecutionPlan, ...]
    metadata: Mapping[str, str | int | bool] = MappingProxyType({})

    def __post_init__(self) -> None:
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def total_timeout_seconds(self) -> int:
        return sum(child.timeout_seconds for child in self.children)

    @property
    def total_output_limit_bytes(self) -> int:
        return sum(
            child.stdout_limit_bytes + child.stderr_limit_bytes
            for child in self.children
        )


@dataclass(frozen=True, slots=True)
class ServerListItem:
    alias: str
    enabled: bool
    project_count: int


@dataclass(frozen=True, slots=True)
class ServerListResult:
    servers: tuple[ServerListItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "servers", tuple(self.servers))


@dataclass(frozen=True, slots=True)
class ProjectListItem:
    alias: str
    services: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "services", tuple(self.services))


@dataclass(frozen=True, slots=True)
class ProjectListResult:
    server_alias: str
    server_enabled: bool
    projects: tuple[ProjectListItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "projects", tuple(self.projects))


def _unsafe() -> ExecutionPlanError:
    return ExecutionPlanError()


def validate_execution_plan(
    plan: ExecutionPlan,
    *,
    trusted_project_paths: frozenset[str] = frozenset(),
    trusted_services: frozenset[str] = frozenset(),
) -> None:
    """Apply defense-in-depth limits to a single fixed argv plan."""
    if (
        not plan.argv
        or plan.argv[0] not in ALLOWED_EXECUTABLES
        or plan.operation not in ALLOWED_PLAN_OPERATIONS
        or _ALIAS_RE.fullmatch(plan.server_alias) is None
    ):
        raise _unsafe()
    if len(plan.argv) > MAX_ARGV_COUNT:
        raise _unsafe()
    for argument in plan.argv:
        if (
            not isinstance(argument, str)
            or not argument
            or len(argument) > MAX_ARGUMENT_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in argument)
            or any(character in argument for character in _SHELL_METACHARACTERS)
            or "$(" in argument
        ):
            raise _unsafe()
    if (
        isinstance(plan.timeout_seconds, bool)
        or not 1 <= plan.timeout_seconds <= MAX_TIMEOUT_SECONDS
        or isinstance(plan.stdout_limit_bytes, bool)
        or not 1 <= plan.stdout_limit_bytes <= MAX_STDOUT_LIMIT_BYTES
        or isinstance(plan.stderr_limit_bytes, bool)
        or not 1 <= plan.stderr_limit_bytes <= MAX_STDERR_LIMIT_BYTES
        or isinstance(plan.max_output_lines, bool)
        or not 1 <= plan.max_output_lines <= MAX_OUTPUT_LINES
    ):
        raise _unsafe()

    if plan.operation in {"project_git_status", "project_last_commit"}:
        if plan.argv.count("-C") != 1:
            raise _unsafe()
        path_index = plan.argv.index("-C") + 1
        if (
            path_index >= len(plan.argv)
            or plan.argv[path_index] not in trusted_project_paths
        ):
            raise _unsafe()
    if plan.operation in {"service_status", "service_recent_logs"}:
        unit = plan.metadata.get("service")
        if not isinstance(unit, str) or unit not in trusted_services:
            raise _unsafe()
        if unit not in plan.argv:
            raise _unsafe()
    if plan.operation == "top_processes":
        sort_by = plan.metadata.get("sort_by")
        limit = plan.metadata.get("limit")
        if (
            sort_by not in {"cpu", "memory"}
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 30
        ):
            raise _unsafe()


def validate_composite_execution_plan(plan: CompositeExecutionPlan) -> None:
    """Reject nesting and excessive aggregate resource limits."""
    if (
        plan.operation not in {"server_summary", "project_summary"}
        or _ALIAS_RE.fullmatch(plan.server_alias) is None
        or any(not isinstance(child, ExecutionPlan) for child in plan.children)
        or any(child.server_alias != plan.server_alias for child in plan.children)
    ):
        raise _unsafe()
    if (
        not plan.children
        or len(plan.children) > MAX_COMPOSITE_CHILDREN
        or plan.total_timeout_seconds > MAX_COMPOSITE_TIMEOUT_SECONDS
        or plan.total_output_limit_bytes > MAX_COMPOSITE_OUTPUT_BYTES
    ):
        raise _unsafe()
