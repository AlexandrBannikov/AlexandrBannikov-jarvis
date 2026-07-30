import asyncio
from pathlib import Path
import shlex

import pytest

from app.ssh_agent.errors import ErrorCode
from app.ssh_agent.execution_plan import (
    ExecutionPlan,
    MAX_OUTPUT_LINES,
    MAX_STDOUT_LIMIT_BYTES,
    MAX_TIMEOUT_SECONDS,
)
from app.ssh_agent.models import ProjectConfig, ServerConfig
from app.ssh_agent.output import ABSOLUTE_STREAM_HARD_CAP, capture_stream
from app.ssh_agent import transport


def server(**changes: object) -> ServerConfig:
    values = {
        "alias": "alpha",
        "host": "127.0.0.1",
        "port": 2222,
        "user": "jarvis-ops",
        "identity_file": Path("/etc/jarvis/ssh/id_ed25519"),
        "host_key_alias": "alpha-local",
        "enabled": True,
        "projects": {
            "app": ProjectConfig("app", Path("/opt/apps/my app"), ("app.service",))
        },
    }
    values.update(changes)
    return ServerConfig(**values)  # type: ignore[arg-type]


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


class FakeStream:
    def __init__(self, chunks: list[bytes] | tuple[bytes, ...]) -> None:
        self.chunks = list(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        await asyncio.sleep(0)
        return self.chunks.pop(0) if self.chunks else b""


class FakeProcess:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int | None = 0,
        block_wait: bool = False,
        block_after_terminate: bool = False,
    ) -> None:
        self.stdout = FakeStream([stdout] if stdout else [])
        self.stderr = FakeStream([stderr] if stderr else [])
        self.returncode = None if block_wait else returncode
        self.final_returncode = returncode
        self.block_wait = block_wait
        self.block_after_terminate = block_after_terminate
        self.terminated = False
        self.killed = False
        self.reaped = False
        self._event = asyncio.Event()

    async def wait(self) -> int:
        if self.block_wait and self.returncode is None:
            await self._event.wait()
        self.reaped = True
        assert self.returncode is not None
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if not self.block_after_terminate:
            self.returncode = -15
            self._event.set()

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9
        self._event.set()


def install_fake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    process: FakeProcess,
) -> dict[str, object]:
    executable = tmp_path / "ssh"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(transport, "SSH_EXECUTABLE", executable)
    captured: dict[str, object] = {}

    async def create(*argv: str, **kwargs: object) -> FakeProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    return captured


@pytest.mark.asyncio
async def test_success_uses_fixed_openssh_argv_and_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess(stdout=b"ok\n")
    captured = install_fake(monkeypatch, tmp_path, process)

    result = await transport.execute(server(), plan())

    assert result.success and result.stdout == "ok\n"
    argv = captured["argv"]
    assert isinstance(argv, tuple)
    assert argv[0] == str(tmp_path / "ssh")
    for option in (
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "KbdInteractiveAuthentication=no",
        "PreferredAuthentications=publickey",
        "StrictHostKeyChecking=yes",
        "UserKnownHostsFile=/etc/jarvis/ssh/known_hosts",
        "IdentitiesOnly=yes",
        "LogLevel=ERROR",
        "ConnectTimeout=8",
        "ServerAliveInterval=5",
        "ServerAliveCountMax=2",
        "HostKeyAlias=alpha-local",
    ):
        assert option in argv
    assert ("-p", "2222") == argv[argv.index("-p") : argv.index("-p") + 2]
    assert argv[argv.index("-i") + 1] == "/etc/jarvis/ssh/id_ed25519"
    assert argv[argv.index("-l") + 1] == "jarvis-ops"
    assert argv[-2] == "127.0.0.1"
    kwargs = captured["kwargs"]
    assert kwargs["stdin"] is asyncio.subprocess.DEVNULL
    assert kwargs["stdout"] is asyncio.subprocess.PIPE
    assert kwargs["stderr"] is asyncio.subprocess.PIPE
    assert kwargs["env"] == {"PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8"}
    assert "SSH_AUTH_SOCK" not in kwargs["env"]
    assert "shell" not in kwargs


def test_remote_encoder_quotes_each_argument_and_round_trips() -> None:
    value = plan(
        operation="project_git_status",
        argv=(
            "/usr/bin/git", "--no-pager", "-c", "credential.interactive=false",
            "-c", "core.hooksPath=/dev/null", "-C", "/opt/apps/my app",
            "status", "--short", "--branch",
        ),
        metadata={"project": "app"},
    )
    encoded = transport._encode_plan(value, server())
    assert shlex.split(encoded) == list(value.argv)
    assert encoded.count("'") >= len(value.argv) * 2
    assert "'/opt/apps/my app'" in encoded


@pytest.mark.parametrize(
    ("sort_by", "sort_argument"),
    [("cpu", "--sort=-%cpu"), ("memory", "--sort=-%mem")],
)
def test_top_process_transport_accepts_only_fixed_ps_shape(
    sort_by: str, sort_argument: str,
) -> None:
    value = plan(
        operation="top_processes",
        argv=(
            "/usr/bin/ps",
            "-eo",
            "pid=,user=,%cpu=,%mem=,etime=,comm=",
            sort_argument,
        ),
        metadata={"sort_by": sort_by, "limit": 10},
    )
    encoded = transport._encode_plan(value, server())
    assert shlex.split(encoded) == list(value.argv)
    assert "args" not in encoded
    assert "cmdline" not in encoded

    with pytest.raises(Exception):
        transport._encode_plan(
            plan(
                operation="top_processes",
                argv=("/usr/bin/ps", "-eo", "pid=,args="),
                metadata={"sort_by": sort_by, "limit": 10},
            ),
            server(),
        )


@pytest.mark.parametrize("payload", ["x;id", "x|id", "$(id)", "x\nid", "x\x00id"])
def test_shell_syntax_cannot_enter_remote_command(payload: str) -> None:
    with pytest.raises(Exception):
        transport._encode_plan(plan(argv=("/bin/df", payload)), server())


@pytest.mark.parametrize(
    "changes",
    [
        {"argv": ("/usr/bin/git", "status")},
        {"timeout_seconds": MAX_TIMEOUT_SECONDS + 1},
        {"stdout_limit_bytes": MAX_STDOUT_LIMIT_BYTES + 1},
        {"max_output_lines": MAX_OUTPUT_LINES + 1},
        {"operation": "unknown"},
        {"server_alias": "other"},
    ],
)
@pytest.mark.asyncio
async def test_forged_plans_are_rejected(changes: dict[str, object]) -> None:
    result = await transport.execute(server(), plan(**changes))
    assert result.error_code is ErrorCode.SSH_PLAN_UNSAFE
    assert not result.success


@pytest.mark.asyncio
async def test_fake_mutable_plan_is_rejected_without_reading_its_fields() -> None:
    class FakePlan:
        @property
        def operation(self) -> str:
            raise AssertionError("forged field was read")

    result = await transport.execute(server(), FakePlan())  # type: ignore[arg-type]

    assert result.error_code is ErrorCode.SSH_PLAN_UNSAFE
    assert result.operation == ""
    assert result.server_alias == "alpha"


@pytest.mark.asyncio
async def test_disabled_server_and_root_account_rejected() -> None:
    disabled = await transport.execute(server(enabled=False), plan())
    root = await transport.execute(server(user="root"), plan())
    assert disabled.error_code is ErrorCode.SSH_SERVER_DISABLED
    assert root.error_code is ErrorCode.SSH_PLAN_UNSAFE


@pytest.mark.asyncio
async def test_missing_executable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(transport, "SSH_EXECUTABLE", tmp_path / "missing")
    result = await transport.execute(server(), plan())
    assert result.error_code is ErrorCode.SSH_EXECUTABLE_NOT_FOUND


@pytest.mark.parametrize(
    ("stderr", "code", "safe"),
    [
        (b"ssh: connect to host secret port 22: Connection refused", ErrorCode.SSH_CONNECTION_REFUSED, "connection refused"),
        (b"ssh: connect to host secret port 22: Connection timed out", ErrorCode.SSH_CONNECTION_TIMEOUT, "connection timed out"),
        (b"Host key verification failed.", ErrorCode.SSH_HOST_KEY_UNKNOWN, "host key not trusted"),
        (
            b"WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n"
            b"Host key verification failed.",
            ErrorCode.SSH_HOST_KEY_MISMATCH,
            "host key changed",
        ),
        (b"admin@secret: Permission denied (publickey).", ErrorCode.SSH_AUTHENTICATION_FAILED, "authentication failed"),
        (b"unknown banner /etc/jarvis/ssh/id_ed25519", ErrorCode.SSH_REMOTE_COMMAND_FAILED, "remote command failed"),
    ],
)
@pytest.mark.asyncio
async def test_error_classification_never_leaks_raw_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stderr: bytes,
    code: ErrorCode,
    safe: str,
) -> None:
    install_fake(monkeypatch, tmp_path, FakeProcess(stderr=stderr, returncode=255))
    result = await transport.execute(server(), plan())
    assert result.error_code is code
    assert result.stderr_safe == safe
    assert "secret" not in result.stderr_safe
    assert "id_ed25519" not in result.stderr_safe


@pytest.mark.asyncio
async def test_invalid_utf8_empty_streams_and_no_final_newline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(monkeypatch, tmp_path, FakeProcess(stdout=b"value\xff"))
    result = await transport.execute(server(), plan())
    assert result.stdout == "value\ufffd"
    assert result.stderr_safe == ""


@pytest.mark.asyncio
async def test_simultaneous_streams_are_drained(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(
        monkeypatch, tmp_path, FakeProcess(stdout=b"ordinary\n", stderr=b"warning")
    )
    result = await transport.execute(server(), plan())
    assert result.stdout == "ordinary\n"
    assert result.output_bytes == len(b"ordinary\nwarning")


@pytest.mark.asyncio
async def test_output_byte_and_line_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install_fake(monkeypatch, tmp_path, FakeProcess(stdout=b"one\ntwo\nthree\n"))
    result = await transport.execute(
        server(), plan(stdout_limit_bytes=10, max_output_lines=2)
    )
    assert result.truncated
    assert result.error_code is ErrorCode.SSH_OUTPUT_TRUNCATED
    assert len(result.stdout.encode()) <= 10
    assert result.stdout == "one\ntwo\n"


@pytest.mark.asyncio
async def test_capture_stream_incremental_and_absolute_cap() -> None:
    stream = FakeStream([b"x" * 100_000] * 20)
    captured = await capture_stream(
        stream, byte_limit=10 * ABSOLUTE_STREAM_HARD_CAP, line_limit=None
    )
    assert len(captured.data) == ABSOLUTE_STREAM_HARD_CAP
    assert captured.observed_bytes == 2_000_000
    assert captured.truncated
    assert all(size > 0 for size in stream.read_sizes)


@pytest.mark.asyncio
async def test_timeout_terminates_and_reaps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess(stdout=b"partial", block_wait=True)
    install_fake(monkeypatch, tmp_path, process)
    result = await transport.execute(server(), plan(timeout_seconds=1))
    assert result.error_code is ErrorCode.SSH_COMMAND_TIMEOUT
    assert result.timed_out and process.terminated and process.reaped
    assert result.stdout == "partial"


@pytest.mark.asyncio
async def test_timeout_kills_after_grace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess(block_wait=True, block_after_terminate=True)
    install_fake(monkeypatch, tmp_path, process)
    monkeypatch.setattr(transport, "TERMINATION_GRACE_SECONDS", 0.001)
    result = await transport.execute(server(), plan(timeout_seconds=1))
    assert result.error_code is ErrorCode.SSH_COMMAND_TIMEOUT
    assert process.terminated and process.killed and process.reaped


@pytest.mark.asyncio
async def test_cancellation_cleans_up_and_is_reraised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    process = FakeProcess(block_wait=True)
    install_fake(monkeypatch, tmp_path, process)
    task = asyncio.create_task(transport.execute(server(), plan()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert process.terminated and process.reaped


@pytest.mark.asyncio
async def test_subprocess_creation_failure_is_stable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executable = tmp_path / "ssh"
    executable.write_text("", encoding="utf-8")
    monkeypatch.setattr(transport, "SSH_EXECUTABLE", executable)

    async def fail(*args: object, **kwargs: object) -> None:
        raise OSError("private /path and host")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fail)
    result = await transport.execute(server(), plan())
    assert result.error_code is ErrorCode.SSH_PROCESS_ERROR
    assert "/path" not in result.stderr_safe


def test_no_shell_or_generic_raw_command_api() -> None:
    source = Path(transport.__file__).read_text(encoding="utf-8")
    forbidden = "create_" + "subprocess_shell"
    shell_kwarg = "shell" + "=True"
    assert forbidden not in source
    assert shell_kwarg not in source
    assert not hasattr(transport, "execute_command")
    assert not hasattr(transport, "execute_raw")
