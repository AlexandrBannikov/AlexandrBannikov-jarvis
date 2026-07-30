"""Secure asynchronous execution of validated fixed plans via OpenSSH."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from pathlib import Path
import re
import time

from .errors import ErrorCode, ExecutionPlanError
from .execution_plan import ExecutionPlan, validate_execution_plan
from .models import ServerConfig
from .output import ABSOLUTE_STREAM_HARD_CAP, CapturedOutput, capture_stream
from .redaction import redact_secrets
from .transport_models import ExecutionResult

SSH_EXECUTABLE = Path("/usr/bin/ssh")
KNOWN_HOSTS_FILE = Path("/etc/jarvis/ssh/known_hosts")
CONNECT_TIMEOUT_SECONDS = 8
SERVER_ALIVE_INTERVAL_SECONDS = 5
SERVER_ALIVE_COUNT_MAX = 2
TERMINATION_GRACE_SECONDS = 0.5
STDERR_TRANSPORT_CAP_BYTES = 32 * 1024
SAFE_STDERR_CHARS = 512
CONTROLLED_ENV: Mapping[str, str] = {
    "PATH": "/usr/bin:/bin",
    "LC_ALL": "C.UTF-8",
}

_LOG = logging.getLogger("jarvis.ssh_agent.transport")
_SAFE_SERVER_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_SAFE_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_SAFE_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}\Z")
_SAFE_HOST_KEY_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


def _quote_posix_argument(argument: str) -> str:
    if "\x00" in argument or "\n" in argument or "\r" in argument:
        raise ExecutionPlanError()
    return "'" + argument.replace("'", "'\"'\"'") + "'"


def _encode_plan(plan: ExecutionPlan, server: ServerConfig) -> str:
    _validate_transport_plan(server, plan)
    return " ".join(_quote_posix_argument(argument) for argument in plan.argv)


def _trusted_plan_shape(server: ServerConfig, plan: ExecutionPlan) -> bool:
    argv = plan.argv
    if plan.operation == "hostname":
        return argv == ("/bin/cat", "/etc/hostname")
    if plan.operation == "disk_usage":
        return argv == ("/bin/df", "-P", "-B1", "/")
    if plan.operation == "memory_usage":
        return argv == ("/bin/cat", "/proc/meminfo")
    if plan.operation == "load_average":
        return argv == ("/bin/cat", "/proc/loadavg")
    if plan.operation == "uptime":
        return argv == ("/bin/cat", "/proc/uptime")
    services = frozenset(
        service for project in server.projects.values() for service in project.services
    )
    paths = frozenset(str(project.path) for project in server.projects.values())
    if plan.operation == "service_status":
        expected_prefix = (
            "/usr/bin/systemctl", "show", "--no-pager",
        )
        return (
            len(argv) == 6
            and argv[:3] == expected_prefix
            and argv[3].startswith("--property=")
            and argv[4] == "--"
            and argv[5] in services
            and plan.metadata.get("service") == argv[5]
        )
    if plan.operation == "service_recent_logs":
        return (
            len(argv) == 7
            and argv[:3] == (
                "/usr/bin/journalctl", "--no-pager", "--output=short-iso"
            )
            and argv[3] == "--lines"
            and argv[4].isdigit()
            and 1 <= int(argv[4]) <= 200
            and argv[5] == "--unit"
            and argv[6] in services
            and plan.metadata.get("service") == argv[6]
            and plan.metadata.get("lines") == int(argv[4])
        )
    git_prefix = (
        "/usr/bin/git", "--no-pager", "-c", "credential.interactive=false",
        "-c", "core.hooksPath=/dev/null", "-C",
    )
    if plan.operation == "project_git_status":
        return (
            len(argv) == 11 and argv[:7] == git_prefix and argv[7] in paths
            and argv[8:] == ("status", "--short", "--branch")
        )
    if plan.operation == "project_last_commit":
        return (
            len(argv) == 12 and argv[:7] == git_prefix and argv[7] in paths
            and argv[8:] == (
                "log", "-1", "--no-decorate", "--format=%h%x00%s%x00%aI"
            )
        )
    return False


def _validate_transport_plan(server: ServerConfig, plan: ExecutionPlan) -> None:
    if (
        type(server) is not ServerConfig
        or type(plan) is not ExecutionPlan
        or not server.enabled
        or plan.server_alias != server.alias
        or _SAFE_SERVER_RE.fullmatch(server.alias) is None
        or server.user == "root"
        or _SAFE_USER_RE.fullmatch(server.user) is None
        or _SAFE_HOST_RE.fullmatch(server.host) is None
        or _SAFE_HOST_KEY_ALIAS_RE.fullmatch(server.host_key_alias) is None
        or not 1 <= server.port <= 65535
        or not server.identity_file.is_absolute()
    ):
        raise ExecutionPlanError()
    paths = frozenset(str(project.path) for project in server.projects.values())
    services = frozenset(
        service for project in server.projects.values() for service in project.services
    )
    validate_execution_plan(
        plan, trusted_project_paths=paths, trusted_services=services
    )
    if not _trusted_plan_shape(server, plan):
        raise ExecutionPlanError()


def _build_ssh_argv(server: ServerConfig, plan: ExecutionPlan) -> tuple[str, ...]:
    remote_command = _encode_plan(plan, server)
    options = (
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "PreferredAuthentications=publickey",
        "StrictHostKeyChecking=yes",
        f"UserKnownHostsFile={KNOWN_HOSTS_FILE}",
        "IdentitiesOnly=yes",
        "LogLevel=ERROR",
        f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}",
        f"ServerAliveInterval={SERVER_ALIVE_INTERVAL_SECONDS}",
        f"ServerAliveCountMax={SERVER_ALIVE_COUNT_MAX}",
        f"HostKeyAlias={server.host_key_alias}",
    )
    argv: list[str] = [str(SSH_EXECUTABLE)]
    for option in options:
        argv.extend(("-o", option))
    argv.extend(
        (
            "-p", str(server.port),
            "-i", str(server.identity_file),
            "-l", server.user,
            "--", server.host,
            remote_command,
        )
    )
    return tuple(argv)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        await process.wait()
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=TERMINATION_GRACE_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()


def _classify(exit_code: int | None, stderr: str) -> ErrorCode:
    lowered = stderr.lower()
    if "connection refused" in lowered:
        return ErrorCode.SSH_CONNECTION_REFUSED
    if "connection timed out" in lowered or "operation timed out" in lowered:
        return ErrorCode.SSH_CONNECTION_TIMEOUT
    if "remote host identification has changed" in lowered:
        return ErrorCode.SSH_HOST_KEY_MISMATCH
    if (
        "host key verification failed" in lowered
        or "no ed25519 host key is known" in lowered
        or "no ecdsa host key is known" in lowered
        or "no rsa host key is known" in lowered
    ):
        return ErrorCode.SSH_HOST_KEY_UNKNOWN
    if "permission denied" in lowered or "authentication failed" in lowered:
        return ErrorCode.SSH_AUTHENTICATION_FAILED
    if exit_code is not None:
        return ErrorCode.SSH_REMOTE_COMMAND_FAILED
    return ErrorCode.SSH_PROCESS_ERROR


def _safe_stderr(error_code: ErrorCode, raw: str) -> str:
    summaries = {
        ErrorCode.SSH_CONNECTION_REFUSED: "connection refused",
        ErrorCode.SSH_CONNECTION_TIMEOUT: "connection timed out",
        ErrorCode.SSH_HOST_KEY_UNKNOWN: "host key not trusted",
        ErrorCode.SSH_HOST_KEY_MISMATCH: "host key changed",
        ErrorCode.SSH_AUTHENTICATION_FAILED: "authentication failed",
        ErrorCode.SSH_REMOTE_COMMAND_FAILED: "remote command failed",
        ErrorCode.SSH_PROCESS_ERROR: "SSH process error",
        ErrorCode.SSH_EXECUTABLE_NOT_FOUND: "SSH executable unavailable",
        ErrorCode.SSH_COMMAND_TIMEOUT: "remote command timed out",
        ErrorCode.SSH_OUTPUT_TRUNCATED: "output truncated",
        ErrorCode.SSH_PLAN_UNSAFE: "unsafe execution plan",
        ErrorCode.SSH_SERVER_DISABLED: "server disabled",
    }
    del raw
    return summaries[error_code][:SAFE_STDERR_CHARS]


def _result(
    plan: ExecutionPlan,
    started: float,
    *,
    exit_code: int | None = None,
    stdout: CapturedOutput = CapturedOutput(b"", 0, False),
    stderr: CapturedOutput = CapturedOutput(b"", 0, False),
    timed_out: bool = False,
    forced_error: ErrorCode | None = None,
) -> ExecutionResult:
    stdout_text = redact_secrets(stdout.data.decode("utf-8", errors="replace"))
    raw_stderr = redact_secrets(stderr.data.decode("utf-8", errors="replace"))
    truncated = stdout.truncated or stderr.truncated
    error = forced_error
    if error is None and exit_code != 0:
        error = _classify(exit_code, raw_stderr)
    if error is None and truncated:
        error = ErrorCode.SSH_OUTPUT_TRUNCATED
    success = exit_code == 0 and error is None and not timed_out
    stderr_safe = "" if success else _safe_stderr(error or ErrorCode.SSH_PROCESS_ERROR, raw_stderr)
    return ExecutionResult(
        operation=plan.operation,
        server_alias=plan.server_alias,
        success=success,
        exit_code=exit_code,
        stdout=stdout_text,
        stderr_safe=stderr_safe,
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        timed_out=timed_out,
        truncated=truncated,
        error_code=error,
        output_bytes=stdout.observed_bytes + stderr.observed_bytes,
    )


def _rejected_result(
    server: object,
    plan: object,
    started: float,
    error: ErrorCode,
) -> ExecutionResult:
    """Return a stable result without reading attributes from forged objects."""
    operation = plan.operation if type(plan) is ExecutionPlan else ""
    server_alias = (
        server.alias
        if type(server) is ServerConfig and _SAFE_SERVER_RE.fullmatch(server.alias)
        else ""
    )
    return ExecutionResult(
        operation=operation,
        server_alias=server_alias,
        success=False,
        exit_code=None,
        stdout="",
        stderr_safe=_safe_stderr(error, ""),
        duration_ms=max(0, int((time.monotonic() - started) * 1000)),
        timed_out=False,
        truncated=False,
        error_code=error,
        output_bytes=0,
    )


async def execute(server: ServerConfig, plan: ExecutionPlan) -> ExecutionResult:
    """Execute one fixed validated plan without exposing a raw command API."""
    started = time.monotonic()
    try:
        _validate_transport_plan(server, plan)
    except (ExecutionPlanError, AttributeError, TypeError, ValueError):
        code = (
            ErrorCode.SSH_SERVER_DISABLED
            if type(server) is ServerConfig and not server.enabled
            else ErrorCode.SSH_PLAN_UNSAFE
        )
        return _rejected_result(server, plan, started, code)
    if not SSH_EXECUTABLE.is_file():
        return _result(
            plan, started, forced_error=ErrorCode.SSH_EXECUTABLE_NOT_FOUND
        )

    try:
        process = await asyncio.create_subprocess_exec(
            *_build_ssh_argv(server, plan),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=dict(CONTROLLED_ENV),
        )
    except FileNotFoundError:
        return _result(
            plan, started, forced_error=ErrorCode.SSH_EXECUTABLE_NOT_FOUND
        )
    except (PermissionError, OSError):
        return _result(plan, started, forced_error=ErrorCode.SSH_PROCESS_ERROR)

    stdout_task = asyncio.create_task(
        capture_stream(
            process.stdout,
            byte_limit=min(plan.stdout_limit_bytes, ABSOLUTE_STREAM_HARD_CAP),
            line_limit=plan.max_output_lines,
        )
    )
    stderr_task = asyncio.create_task(
        capture_stream(
            process.stderr,
            byte_limit=min(plan.stderr_limit_bytes, STDERR_TRANSPORT_CAP_BYTES),
            line_limit=None,
        )
    )
    timed_out = False
    try:
        try:
            await asyncio.wait_for(process.wait(), timeout=plan.timeout_seconds)
        except TimeoutError:
            timed_out = True
            await _stop_process(process)
        stdout, stderr = await asyncio.gather(stdout_task, stderr_task)
    except asyncio.CancelledError:
        await _stop_process(process)
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        raise
    except Exception:
        await _stop_process(process)
        stdout_task.cancel()
        stderr_task.cancel()
        await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return _result(plan, started, forced_error=ErrorCode.SSH_PROCESS_ERROR)

    result = _result(
        plan,
        started,
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        forced_error=ErrorCode.SSH_COMMAND_TIMEOUT if timed_out else None,
    )
    _LOG.info(
        "ssh_execution",
        extra={
            "server_alias": result.server_alias,
            "operation": result.operation,
            "result_code": result.error_code.value if result.error_code else "OK",
            "duration_ms": result.duration_ms,
            "exit_code": result.exit_code,
            "stdout_bytes": stdout.observed_bytes,
            "stderr_bytes": stderr.observed_bytes,
            "truncated": result.truncated,
            "timed_out": result.timed_out,
        },
    )
    return result
