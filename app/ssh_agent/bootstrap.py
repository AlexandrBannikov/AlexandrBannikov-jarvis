"""Fail-closed construction of one shared SSH dependency graph."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app.tools.registry import ToolRegistry

from .config import load_config
from .errors import ConfigError, ErrorCode
from .limits import ConcurrencyLimiter, RateLimiter
from .metrics import SSHMetrics
from .models import SSHAgentConfig
from .readiness import SSHReadiness, validate_runtime
from .registry import ServerRegistry
from .service import SSHService
from .tools import register_ssh_tools
from .transport import KNOWN_HOSTS_FILE, SSH_EXECUTABLE


@dataclass(frozen=True, slots=True)
class SSHDependencies:
    service: SSHService
    registry: ServerRegistry
    metrics: SSHMetrics
    readiness: SSHReadiness


def _config_failure(error: ConfigError) -> ErrorCode:
    if error.code is ErrorCode.CONFIG_NOT_FOUND:
        return ErrorCode.SSH_CONFIG_MISSING
    if error.code is ErrorCode.CONFIG_UNSAFE:
        return ErrorCode.SSH_CONFIG_PERMISSIONS_UNSAFE
    return ErrorCode.SSH_CONFIG_INVALID


def build_ssh_dependencies(
    *,
    enabled: bool,
    config_path: Path,
    tool_registry: ToolRegistry | None = None,
    known_hosts_path: Path = KNOWN_HOSTS_FILE,
    ssh_executable: Path = SSH_EXECUTABLE,
    rate_limiter: RateLimiter | None = None,
    concurrency_limiter: ConcurrencyLimiter | None = None,
) -> SSHDependencies:
    metrics = SSHMetrics()
    config = SSHAgentConfig(1, {})
    readiness = SSHReadiness.disabled()
    if enabled:
        try:
            config = load_config(config_path)
        except ConfigError as error:
            readiness = SSHReadiness(True, False, _config_failure(error))
        else:
            readiness = validate_runtime(
                config, config_path=config_path,
                known_hosts_path=known_hosts_path,
                ssh_executable=ssh_executable,
            )
    registry = ServerRegistry(config)
    service = SSHService(
        registry,
        enabled=readiness.ready,
        availability_error=(
            ErrorCode.SSH_DISABLED if not enabled else readiness.code
        ),
        rate_limiter=rate_limiter,
        concurrency_limiter=concurrency_limiter,
        metrics=metrics,
    )
    if tool_registry is not None:
        register_ssh_tools(tool_registry, service)
    return SSHDependencies(service, registry, metrics, readiness)
