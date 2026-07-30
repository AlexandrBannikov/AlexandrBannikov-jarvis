"""Immutable public results returned by the OpenSSH transport."""

from dataclasses import dataclass

from .errors import ErrorCode


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    operation: str
    server_alias: str
    success: bool
    exit_code: int | None
    stdout: str
    stderr_safe: str
    duration_ms: int
    timed_out: bool
    truncated: bool
    error_code: ErrorCode | None
    output_bytes: int
