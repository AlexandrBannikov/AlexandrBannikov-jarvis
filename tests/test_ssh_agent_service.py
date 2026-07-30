import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.ssh_agent.errors import ErrorCode
from app.ssh_agent.formatter import format_result
from app.ssh_agent.limits import BusyError, ConcurrencyLimiter, RateLimiter
from app.ssh_agent.models import ProjectConfig, SSHAgentConfig, ServerConfig
from app.ssh_agent.parsers import (
    parse_disk, parse_git_status, parse_last_commit, parse_load, parse_memory,
    parse_processes, parse_service, parse_uptime,
)
from app.ssh_agent.registry import ServerRegistry
from app.ssh_agent.service import SSHService, parse_feature_flag, ssh_enabled_from_environment
from app.ssh_agent.service_models import SSHRequestContext, SSHServiceResult
from app.ssh_agent.transport_models import ExecutionResult


def registry() -> ServerRegistry:
    project = ProjectConfig("app", Path("/opt/app"), ("app.service", "app.timer"))
    server = ServerConfig("alpha", "secret.internal", 22, "jarvis-ops",
                          Path("/keys/secret"), "alpha", True, {"app": project})
    disabled = ServerConfig("off", "off.internal", 22, "jarvis-ops",
                            Path("/keys/off"), "off", False, {})
    return ServerRegistry(SSHAgentConfig(1, {"alpha": server, "off": disabled}))


def context(**changes: object) -> SSHRequestContext:
    values = dict(user_id=123, chat_id=456, request_id="req-1",
                  requested_at=datetime.now(timezone.utc), is_allowlisted=True)
    values.update(changes)
    return SSHRequestContext(**values)


OUTPUTS = {
    "hostname": "alpha\n",
    "disk_usage": "Filesystem 1-blocks Used Available Use% Mounted on\n/dev/x 1000 410 590 41% /\n",
    "memory_usage": "MemTotal: 2000 kB\nMemAvailable: 1200 kB\n",
    "load_average": "0.08 0.05 0.01 1/2 3\n",
    "uptime": "1044000.00 1.00\n",
    "service_status": "Id=app.service\nLoadState=loaded\nActiveState=active\nSubState=running\nUnitFileState=enabled\nResult=success\nExecMainStatus=0\n",
    "service_recent_logs": "2026-01-01 safe line\n",
    "project_git_status": "## main...origin/main [ahead 1]\n M file.py\n",
    "project_last_commit": "abc1234\x00message\x002026-01-01T00:00:00Z\n",
    "top_processes": (
        "101 root 4.2 6.8 12-03:04:05 python3\n"
        "202 nobody 1.1 3.4 05:06 xray\n"
    ),
}


class FakeTransport:
    def __init__(self) -> None:
        self.calls = []

    async def __call__(self, server, plan):
        self.calls.append((server.alias, plan))
        return ExecutionResult(plan.operation, server.alias, True, 0,
                               OUTPUTS[plan.operation], "", 2, False, False, None, 20)


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_feature_flag_explicit_true(value: str) -> None:
    assert parse_feature_flag(value)


@pytest.mark.parametrize("value", [None, "", "0", "false", "invalid"])
def test_feature_flag_fails_closed(value: str | None) -> None:
    assert not parse_feature_flag(value)


def test_named_environment_feature_flag() -> None:
    assert ssh_enabled_from_environment({"JARVIS_SSH_ENABLED": "true"})
    assert not ssh_enabled_from_environment({})
    assert not ssh_enabled_from_environment({"JARVIS_SSH_ENABLED": "tru"})


@pytest.mark.asyncio
async def test_context_authorization_and_disabled_precede_lookup() -> None:
    transport = FakeTransport()
    service = SSHService(registry(), enabled=True, transport=transport)
    assert (await service.get_disk_usage(None, "missing")).error_code is ErrorCode.SSH_CONTEXT_INVALID
    denied = await service.get_disk_usage(context(is_allowlisted=False), "missing")
    assert denied.error_code is ErrorCode.SSH_ACCESS_DENIED
    disabled = await SSHService(registry(), transport=transport).get_disk_usage(context(), "alpha")
    assert disabled.error_code is ErrorCode.SSH_DISABLED
    assert not transport.calls


def test_rate_limiter_burst_refill_and_bound() -> None:
    now = [0.0]
    limiter = RateLimiter(60, 3, max_users=2, clock=lambda: now[0])
    assert [limiter.allow(1) for _ in range(4)] == [True, True, True, False]
    now[0] = 1
    assert limiter.allow(1)
    limiter.allow(2)
    limiter.allow(3)
    assert limiter.tracked_users == 2
    now[0] = 1000
    limiter.allow(4)
    assert limiter.tracked_users == 1


@pytest.mark.asyncio
async def test_rate_limit_does_not_call_transport() -> None:
    transport = FakeTransport()
    service = SSHService(registry(), enabled=True, transport=transport,
                         rate_limiter=RateLimiter(1, 1, clock=lambda: 0),)
    assert (await service.get_uptime(context(), "alpha")).success
    assert (await service.get_uptime(context(request_id="req-2"), "alpha")).error_code is ErrorCode.SSH_RATE_LIMITED
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_concurrency_global_user_server_and_release() -> None:
    limiter = ConcurrencyLimiter(2, 1, 1)
    async with limiter.permit(1, "alpha"):
        with pytest.raises(BusyError):
            async with limiter.permit(1, "beta"):
                pass
        with pytest.raises(BusyError):
            async with limiter.permit(2, "alpha"):
                pass
    async with limiter.permit(2, "alpha"):
        assert limiter.active == 1
    assert limiter.active == 0


@pytest.mark.asyncio
async def test_concurrency_released_on_cancellation() -> None:
    limiter = ConcurrencyLimiter(1, 1, 1)
    entered = asyncio.Event()

    async def worker():
        async with limiter.permit(1, "alpha"):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(worker())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert limiter.active == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "args", "operation"),
    [
        ("get_disk_usage", ("alpha",), "disk_usage"),
        ("get_memory_usage", ("alpha",), "memory_usage"),
        ("get_load_average", ("alpha",), "load_average"),
        ("get_uptime", ("alpha",), "uptime"),
        ("get_top_processes", ("alpha", "cpu", 2), "top_processes"),
        ("get_service_status", ("alpha", "app", "app.service"), "service_status"),
        ("get_service_recent_logs", ("alpha", "app", "app.service"), "service_recent_logs"),
        ("get_project_status", ("alpha", "app"), "project_git_status"),
        ("get_project_last_commit", ("alpha", "app"), "project_last_commit"),
    ],
)
async def test_every_single_remote_method(method: str, args: tuple, operation: str) -> None:
    transport = FakeTransport()
    service = SSHService(registry(), enabled=True, transport=transport)
    result = await getattr(service, method)(context(), *args)
    assert result.success and result.operation == operation and result.data
    assert transport.calls[0][1].operation == operation


@pytest.mark.asyncio
async def test_lists_and_authorized_aliases_only() -> None:
    transport = FakeTransport()
    service = SSHService(registry(), enabled=True, transport=transport)
    servers = await service.list_servers(context())
    projects = await service.list_projects(context(request_id="req-2"), "alpha")
    assert {x["alias"] for x in servers.data["items"]} == {"alpha", "off"}
    assert projects.data["items"][0]["alias"] == "app"
    assert not transport.calls


@pytest.mark.asyncio
async def test_composite_server_and_project_partial() -> None:
    class Partial(FakeTransport):
        async def __call__(self, server, plan):
            result = await super().__call__(server, plan)
            if plan.operation == "project_last_commit":
                return ExecutionResult(plan.operation, server.alias, False, 1, "", "safe", 1,
                                       False, False, ErrorCode.SSH_REMOTE_COMMAND_FAILED, 4)
            return result
    service = SSHService(registry(), enabled=True, transport=Partial(),
                         rate_limiter=RateLimiter(100, 20))
    server = await service.get_server_summary(context(), "alpha")
    project = await service.get_project_summary(context(request_id="req-2"), "alpha", "app")
    assert server.success and not server.partial and len(server.data["results"]) == 5
    assert project.success and project.partial and len(project.data["results"]) == 4
    assert "Часть данных недоступна" in format_result(project)


@pytest.mark.asyncio
async def test_unapproved_service_and_disabled_server_do_not_execute() -> None:
    transport = FakeTransport()
    service = SSHService(registry(), enabled=True, transport=transport)
    bad = await service.get_service_status(context(), "alpha", "app", "evil.service")
    off = await service.get_disk_usage(context(request_id="req-2"), "off")
    assert not bad.success and not off.success and not transport.calls


def test_fixed_output_parsers_valid_and_malformed() -> None:
    assert parse_disk(OUTPUTS["disk_usage"])["percent_used"] == 41
    assert parse_memory(OUTPUTS["memory_usage"])["used_bytes"] == 800 * 1024
    assert parse_load(OUTPUTS["load_average"])["load_5"] == .05
    assert parse_uptime(OUTPUTS["uptime"])["uptime_seconds"] == 1044000
    assert parse_service(OUTPUTS["service_status"])["active_state"] == "active"
    assert not parse_git_status("## main\n")["clean"] is False
    assert parse_last_commit(OUTPUTS["project_last_commit"])["short_hash"] == "abc1234"
    for parser in (parse_disk, parse_memory, parse_load, parse_uptime, parse_last_commit):
        with pytest.raises((ValueError, KeyError, IndexError)):
            parser("malformed")
    assert parse_service("missing")["active_state"] is None


def test_parse_processes_is_bounded_and_never_returns_cmdline() -> None:
    parsed = parse_processes(OUTPUTS["top_processes"], 1)
    assert parsed == {
        "count": 1,
        "processes": (
            {
                "pid": 101,
                "user": "root",
                "cpu_percent": 4.2,
                "memory_percent": 6.8,
                "elapsed": "12-03:04:05",
                "command": "python3",
            },
        ),
    }
    assert "args" not in str(parsed).lower()
    assert "cmdline" not in str(parsed).lower()


def test_parse_processes_empty_and_malformed() -> None:
    assert parse_processes("", 5) == {"count": 0, "processes": ()}
    with pytest.raises(ValueError):
        parse_processes("broken process line", 5)
    with pytest.raises(ValueError):
        parse_processes("1 root 1.0 2.0 00:01 /usr/bin/python", 5)


@pytest.mark.asyncio
async def test_top_process_service_adds_safe_context_and_formats() -> None:
    transport = FakeTransport()
    service = SSHService(registry(), enabled=True, transport=transport)
    result = await service.get_top_processes(context(), "alpha", "memory", 2)
    assert result.success
    assert result.data["server_alias"] == "alpha"
    assert result.data["sort_by"] == "memory"
    assert result.data["count"] == 2
    assert all(
        set(item) == {
            "pid", "user", "cpu_percent", "memory_percent", "elapsed",
            "command",
        }
        for item in result.data["processes"]
    )
    assert "python3" in format_result(result)


@pytest.mark.asyncio
async def test_top_process_timeout_is_mapped_and_audited(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class TimeoutTransport:
        async def __call__(self, server, plan):
            return ExecutionResult(
                plan.operation, server.alias, False, 255, "", "", 0,
                False, True, ErrorCode.SSH_COMMAND_TIMEOUT, 10_000,
            )

    service = SSHService(registry(), enabled=True, transport=TimeoutTransport())
    with caplog.at_level("INFO", logger="jarvis.ssh_agent.service"):
        result = await service.get_top_processes(
            context(), "alpha", "cpu", 5
        )

    assert not result.success
    assert result.error_code is ErrorCode.SSH_COMMAND_TIMEOUT
    records = [
        record for record in caplog.records
        if record.getMessage() == "ssh_service_request"
    ]
    assert records
    assert records[-1].operation == "top_processes"
    assert records[-1].result_code == "SSH_COMMAND_TIMEOUT"


def test_formatter_redacts_and_never_exposes_config() -> None:
    result = SSHServiceResult(True, "service_recent_logs", "alpha", service_name="app.service",
                              data={"lines": ("Authorization: " + "Bearer abcdefghijklmnop",)},
                              truncated=True, partial=True)
    text = format_result(result)
    assert "[REDACTED]" in text and "secret.internal" not in text
    assert "/keys/secret" not in text and "jarvis-ops" not in text
    assert "Вывод сокращён" in text and "Часть данных недоступна" in text
