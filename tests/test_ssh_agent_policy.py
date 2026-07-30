from dataclasses import fields
from pathlib import Path

import pytest

from app.ssh_agent.errors import (
    ErrorCode,
    OperationPolicyError,
    ProjectNotFoundError,
    ServerDisabledError,
    ServerNotFoundError,
    ServiceNotAllowedError,
)
from app.ssh_agent.execution_plan import (
    CompositeExecutionPlan,
    ExecutionPlan,
    ProjectListResult,
    ServerListResult,
)
from app.ssh_agent.models import ProjectConfig, SSHAgentConfig, ServerConfig
from app.ssh_agent.operations import OPERATION_CATALOG, OperationName
from app.ssh_agent.policy import CommandPolicy
from app.ssh_agent.registry import ServerRegistry


def make_policy(*, service_count: int = 2) -> CommandPolicy:
    app = ProjectConfig(
        "app",
        Path("/opt/app"),
        tuple(f"app-{index}.service" for index in range(service_count)),
    )
    other = ProjectConfig(
        "other", Path("/opt/other"), ("other.service",)
    )
    enabled = ServerConfig(
        "alpha",
        "localhost",
        22,
        "jarvis-ops",
        Path("/keys/id"),
        "alpha",
        True,
        {"app": app, "other": other},
    )
    disabled = ServerConfig(
        "beta",
        "localhost",
        22,
        "jarvis-ops",
        Path("/keys/id"),
        "beta",
        False,
        {"app": app},
    )
    return CommandPolicy(
        ServerRegistry(SSHAgentConfig(1, {"alpha": enabled, "beta": disabled}))
    )


def test_catalog_contains_exact_supported_operations() -> None:
    assert set(OPERATION_CATALOG) == {operation.value for operation in OperationName}
    assert len(OPERATION_CATALOG) == 13


def test_unknown_operation_rejected() -> None:
    with pytest.raises(OperationPolicyError) as caught:
        make_policy().build_plan("run_command", "alpha")
    assert caught.value.code == ErrorCode.OPERATION_NOT_SUPPORTED


@pytest.mark.parametrize(
    ("operation", "argv"),
    [
        ("disk_usage", ("/bin/df", "-P", "-B1", "/")),
        ("memory_usage", ("/bin/cat", "/proc/meminfo")),
        ("load_average", ("/bin/cat", "/proc/loadavg")),
        ("uptime", ("/bin/cat", "/proc/uptime")),
        (
            "project_git_status",
            (
                "/usr/bin/git",
                "--no-pager",
                "-c",
                "credential.interactive=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                "/opt/app",
                "status",
                "--short",
                "--branch",
            ),
        ),
        (
            "project_last_commit",
            (
                "/usr/bin/git",
                "--no-pager",
                "-c",
                "credential.interactive=false",
                "-c",
                "core.hooksPath=/dev/null",
                "-C",
                "/opt/app",
                "log",
                "-1",
                "--no-decorate",
                "--format=%h%x00%s%x00%aI",
            ),
        ),
    ],
)
def test_exact_fixed_argv(operation: str, argv: tuple[str, ...]) -> None:
    kwargs = {"project_alias": "app"} if operation.startswith("project_") else {}
    plan = make_policy().build_plan(operation, "alpha", **kwargs)
    assert isinstance(plan, ExecutionPlan)
    assert plan.argv == argv
    assert make_policy().build_plan(operation, "alpha", **kwargs) == plan


def test_exact_service_status_argv() -> None:
    plan = make_policy().build_plan(
        "service_status",
        "alpha",
        project_alias="app",
        service_name="app-0.service",
    )
    assert isinstance(plan, ExecutionPlan)
    assert plan.argv == (
        "/usr/bin/systemctl",
        "show",
        "--no-pager",
        "--property=Id,LoadState,ActiveState,SubState,UnitFileState,Result,"
        "ExecMainStatus,ActiveEnterTimestamp,InactiveEnterTimestamp",
        "--",
        "app-0.service",
    )


def test_exact_journal_argv_and_default_lines() -> None:
    plan = make_policy().build_plan(
        "service_recent_logs",
        "alpha",
        project_alias="app",
        service_name="app-0.service",
    )
    assert isinstance(plan, ExecutionPlan)
    assert plan.argv == (
        "/usr/bin/journalctl",
        "--no-pager",
        "--output=short-iso",
        "--lines",
        "50",
        "--unit",
        "app-0.service",
    )
    assert plan.sensitive_output


@pytest.mark.parametrize(
    ("sort_by", "sort_argument"),
    [("cpu", "--sort=-%cpu"), ("memory", "--sort=-%mem")],
)
def test_exact_top_processes_argv(
    sort_by: str, sort_argument: str,
) -> None:
    plan = make_policy().build_plan(
        "top_processes", "alpha", sort_by=sort_by, limit=7
    )
    assert isinstance(plan, ExecutionPlan)
    assert plan.argv == (
        "/usr/bin/ps",
        "-eo",
        "pid=,user=,%cpu=,%mem=,etime=,comm=",
        sort_argument,
    )
    assert plan.metadata == {"sort_by": sort_by, "limit": 7}


@pytest.mark.parametrize("lines", [1, 200])
def test_journal_line_boundaries_accepted(lines: int) -> None:
    plan = make_policy().build_plan(
        "service_recent_logs",
        "alpha",
        project_alias="app",
        service_name="app-0.service",
        lines=lines,
    )
    assert isinstance(plan, ExecutionPlan)
    assert plan.argv[4] == str(lines)


@pytest.mark.parametrize("lines", [0, 201, -1, 1.5, "50", True])
def test_invalid_journal_line_limits(lines: object) -> None:
    with pytest.raises(OperationPolicyError) as caught:
        make_policy().build_plan(
            "service_recent_logs",
            "alpha",
            project_alias="app",
            service_name="app-0.service",
            lines=lines,  # type: ignore[arg-type]
        )
    assert caught.value.code == ErrorCode.INVALID_LINE_LIMIT


@pytest.mark.parametrize(
    ("sort_by", "limit", "code"),
    [
        ("disk", 5, ErrorCode.INVALID_PROCESS_SORT),
        (None, 5, ErrorCode.INVALID_PROCESS_SORT),
        ("cpu", 0, ErrorCode.INVALID_PROCESS_LIMIT),
        ("memory", 31, ErrorCode.INVALID_PROCESS_LIMIT),
        ("cpu", True, ErrorCode.INVALID_PROCESS_LIMIT),
    ],
)
def test_invalid_top_process_parameters(
    sort_by: object, limit: object, code: ErrorCode,
) -> None:
    with pytest.raises(OperationPolicyError) as caught:
        make_policy().build_plan(
            "top_processes", "alpha",
            sort_by=sort_by, limit=limit,  # type: ignore[arg-type]
        )
    assert caught.value.code == code


@pytest.mark.parametrize(
    ("operation", "kwargs", "code"),
    [
        ("project_git_status", {}, ErrorCode.OPERATION_PARAMETER_REQUIRED),
        (
            "disk_usage",
            {"project_alias": "app"},
            ErrorCode.OPERATION_PARAMETER_FORBIDDEN,
        ),
        ("service_status", {"project_alias": "app"}, ErrorCode.OPERATION_PARAMETER_REQUIRED),
        (
            "uptime",
            {"service_name": "app-0.service"},
            ErrorCode.OPERATION_PARAMETER_FORBIDDEN,
        ),
        ("uptime", {"lines": 10}, ErrorCode.OPERATION_PARAMETER_FORBIDDEN),
    ],
)
def test_required_and_forbidden_parameters(
    operation: str, kwargs: dict[str, object], code: ErrorCode
) -> None:
    with pytest.raises(OperationPolicyError) as caught:
        make_policy().build_plan(operation, "alpha", **kwargs)  # type: ignore[arg-type]
    assert caught.value.code == code


def test_arbitrary_keyword_is_not_accepted() -> None:
    with pytest.raises(TypeError):
        make_policy().build_plan(
            "uptime", "alpha", command="id"  # type: ignore[call-arg]
        )


def test_registry_authorization() -> None:
    policy = make_policy()
    assert isinstance(policy.build_plan("uptime", "alpha"), ExecutionPlan)
    with pytest.raises(ServerDisabledError):
        policy.build_plan("uptime", "beta")
    with pytest.raises(ServerNotFoundError):
        policy.build_plan("uptime", "missing")
    with pytest.raises(ProjectNotFoundError):
        policy.build_plan("project_git_status", "alpha", project_alias="missing")
    with pytest.raises(ServiceNotAllowedError):
        policy.build_plan(
            "service_status",
            "alpha",
            project_alias="app",
            service_name="other.service",
        )
    with pytest.raises(ServiceNotAllowedError):
        policy.build_plan(
            "service_status",
            "alpha",
            project_alias="app",
            service_name="fake.service",
        )


@pytest.mark.parametrize(
    "malicious",
    [
        "x; id",
        "$(id)",
        "`id`",
        "x|id",
        "x>file",
        "x\nid",
        "x\rid",
        "x\u0000id",
        "../app",
        "-option",
    ],
)
def test_injection_values_cannot_select_resources(malicious: str) -> None:
    policy = make_policy()
    with pytest.raises((ProjectNotFoundError, ServerNotFoundError)):
        policy.build_plan(
            "project_git_status", malicious, project_alias=malicious
        )
    with pytest.raises((ProjectNotFoundError, ServiceNotAllowedError)):
        policy.build_plan(
            "service_status",
            "alpha",
            project_alias=malicious,
            service_name=malicious,
        )


def test_server_summary_is_flat_and_fixed() -> None:
    plan = make_policy().build_server_summary("alpha")
    assert isinstance(plan, CompositeExecutionPlan)
    assert tuple(child.operation for child in plan.children) == (
        "hostname",
        "uptime",
        "load_average",
        "memory_usage",
        "disk_usage",
    )
    assert plan.children[0].argv == ("/bin/cat", "/etc/hostname")
    assert all(isinstance(child.argv, tuple) for child in plan.children)


def test_project_summary_contains_only_configured_services() -> None:
    plan = make_policy().build_project_summary("alpha", "app")
    assert tuple(child.operation for child in plan.children) == (
        "project_git_status",
        "project_last_commit",
        "service_status",
        "service_status",
    )
    service_argv = [child.argv[-1] for child in plan.children[2:]]
    assert service_argv == ["app-0.service", "app-1.service"]
    assert all(not isinstance(child.argv, str) for child in plan.children)


def test_project_summary_service_limit_enforced() -> None:
    with pytest.raises(Exception) as caught:
        make_policy(service_count=33).build_project_summary("alpha", "app")
    assert getattr(caught.value, "code") == ErrorCode.EXECUTION_PLAN_UNSAFE


def test_local_server_result_hides_connection_details() -> None:
    result = make_policy().build_plan("list_servers")
    assert isinstance(result, ServerListResult)
    assert result.servers[0].alias == "alpha"
    assert not {
        "host",
        "user",
        "identity_file",
        "host_key_alias",
        "path",
    } & {field.name for field in fields(result.servers[0])}
    with pytest.raises(AttributeError):
        result.servers.append(result.servers[0])  # type: ignore[attr-defined]


def test_local_server_result_rejects_unused_server_parameter() -> None:
    with pytest.raises(OperationPolicyError) as caught:
        make_policy().build_plan("list_servers", "alpha")
    assert caught.value.code == ErrorCode.OPERATION_PARAMETER_FORBIDDEN


def test_local_project_result_hides_paths_and_handles_disabled() -> None:
    result = make_policy().build_plan("list_projects", "beta")
    assert isinstance(result, ProjectListResult)
    assert not result.server_enabled
    assert result.projects[0].services == ("app-0.service", "app-1.service")
    assert "path" not in {field.name for field in fields(result.projects[0])}
    with pytest.raises(AttributeError):
        result.projects[0].services.append("x")  # type: ignore[attr-defined]


def test_building_entire_catalog_never_calls_subprocess_or_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("execution or network access attempted")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.socket", forbidden)
    policy = make_policy()
    calls = {
        "server_summary": {},
        "disk_usage": {},
        "memory_usage": {},
        "load_average": {},
        "uptime": {},
        "service_status": {
            "project_alias": "app",
            "service_name": "app-0.service",
        },
        "service_recent_logs": {
            "project_alias": "app",
            "service_name": "app-0.service",
        },
        "project_git_status": {"project_alias": "app"},
        "project_last_commit": {"project_alias": "app"},
        "project_summary": {"project_alias": "app"},
        "list_projects": {},
    }
    policy.build_plan("list_servers")
    for operation, kwargs in calls.items():
        policy.build_plan(operation, "alpha", **kwargs)
