import pytest

from app.ssh_agent.errors import ExecutionPlanError
from app.ssh_agent.execution_plan import (
    CompositeExecutionPlan,
    ExecutionPlan,
    MAX_ARGV_COUNT,
    MAX_COMPOSITE_CHILDREN,
    MAX_OUTPUT_LINES,
    MAX_STDOUT_LIMIT_BYTES,
    MAX_TIMEOUT_SECONDS,
    validate_composite_execution_plan,
    validate_execution_plan,
)


def plan(**changes: object) -> ExecutionPlan:
    values = {
        "operation": "disk_usage",
        "server_alias": "alpha",
        "argv": ("/bin/df", "-P", "-B1", "/"),
        "timeout_seconds": 10,
        "stdout_limit_bytes": 64_000,
        "stderr_limit_bytes": 16_384,
        "max_output_lines": 500,
    }
    values.update(changes)
    return ExecutionPlan(**values)  # type: ignore[arg-type]


def test_allowed_plan_and_defensive_copy() -> None:
    argv = ["/bin/df", "-P", "-B1", "/"]
    value = plan(argv=argv)
    argv.append("untrusted")
    validate_execution_plan(value)
    assert value.argv == ("/bin/df", "-P", "-B1", "/")


@pytest.mark.parametrize(
    "changes",
    [
        {"argv": ()},
        {"argv": ("/tmp/tool",)},
        {"argv": ("/bin/df", "x\nid")},
        {"argv": ("/bin/df", "x\u0000id")},
        {"argv": ("/bin/df", "x;id")},
        {"argv": tuple(["/bin/df"] + ["x"] * MAX_ARGV_COUNT)},
        {"timeout_seconds": MAX_TIMEOUT_SECONDS + 1},
        {"stdout_limit_bytes": MAX_STDOUT_LIMIT_BYTES + 1},
        {"max_output_lines": MAX_OUTPUT_LINES + 1},
    ],
)
def test_unsafe_single_plan_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ExecutionPlanError):
        validate_execution_plan(plan(**changes))


def test_untrusted_path_and_service_rejected() -> None:
    git = plan(
        operation="project_git_status",
        argv=("/usr/bin/git", "--no-pager", "-C", "/tmp/fake", "status"),
    )
    with pytest.raises(ExecutionPlanError):
        validate_execution_plan(git, trusted_project_paths=frozenset({"/opt/app"}))

    service = plan(
        operation="service_status",
        argv=("/usr/bin/systemctl", "show", "--", "fake.service"),
        metadata={"service": "fake.service"},
    )
    with pytest.raises(ExecutionPlanError):
        validate_execution_plan(
            service, trusted_services=frozenset({"app.service"})
        )


def test_composite_limits_and_depth() -> None:
    child = plan(timeout_seconds=1, stdout_limit_bytes=1, stderr_limit_bytes=1)
    validate_composite_execution_plan(
        CompositeExecutionPlan("server_summary", "alpha", (child,))
    )
    with pytest.raises(ExecutionPlanError):
        validate_composite_execution_plan(
            CompositeExecutionPlan(
                "server_summary",
                "alpha",
                tuple(child for _ in range(MAX_COMPOSITE_CHILDREN + 1)),
            )
        )
    with pytest.raises(ExecutionPlanError):
        validate_composite_execution_plan(
            CompositeExecutionPlan(
                "server_summary",
                "alpha",
                (CompositeExecutionPlan("nested", "alpha", (child,)),),  # type: ignore[arg-type]
            )
        )
