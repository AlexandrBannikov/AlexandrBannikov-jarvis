"""Local, network-free SSH runtime readiness validation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat

from .errors import ErrorCode
from .models import SSHAgentConfig
from .transport import KNOWN_HOSTS_FILE, SSH_EXECUTABLE


@dataclass(frozen=True, slots=True)
class SSHReadiness:
    enabled: bool
    ready: bool
    code: ErrorCode
    configuration_ok: bool = False
    known_hosts_ok: bool = False
    key_permissions_ok: bool = False
    executable_ok: bool = False
    registered_servers_count: int = 0
    enabled_servers_count: int = 0

    @classmethod
    def disabled(cls) -> "SSHReadiness":
        return cls(False, False, ErrorCode.SSH_DISABLED)


def _safe_regular(path: Path, *, private: bool) -> tuple[bool, bool]:
    """Return (exists, safe) without opening or reading the file."""
    try:
        metadata = path.lstat()
    except OSError:
        return False, False
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return True, False
    unsafe_write = metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    unsafe_private = private and metadata.st_mode & (
        stat.S_IRWXG | stat.S_IRWXO
    )
    readable = bool(metadata.st_mode & stat.S_IRUSR) and os.access(path, os.R_OK)
    return True, not unsafe_write and not unsafe_private and readable


def validate_runtime(
    config: SSHAgentConfig,
    *,
    config_path: Path,
    known_hosts_path: Path = KNOWN_HOSTS_FILE,
    ssh_executable: Path = SSH_EXECUTABLE,
) -> SSHReadiness:
    servers = tuple(config.servers.values())
    enabled = tuple(server for server in servers if server.enabled)
    base = dict(
        enabled=True,
        registered_servers_count=len(servers),
        enabled_servers_count=len(enabled),
    )
    exists, config_ok = _safe_regular(config_path, private=False)
    if not exists:
        return SSHReadiness(True, False, ErrorCode.SSH_CONFIG_MISSING)
    if not config_ok:
        return SSHReadiness(**base, ready=False,
                            code=ErrorCode.SSH_CONFIG_PERMISSIONS_UNSAFE)

    executable_ok = (
        ssh_executable.is_file() and not ssh_executable.is_symlink()
        and os.access(ssh_executable, os.X_OK)
    )
    if not executable_ok:
        return SSHReadiness(**base, ready=False,
                            code=ErrorCode.SSH_EXECUTABLE_MISSING,
                            configuration_ok=True)

    keys_ok = True
    for server in enabled:
        key_exists, key_ok = _safe_regular(server.identity_file, private=True)
        if not key_exists:
            return SSHReadiness(
                **base, ready=False, code=ErrorCode.SSH_IDENTITY_FILE_MISSING,
                configuration_ok=True, executable_ok=True,
            )
        if not key_ok:
            keys_ok = False
            return SSHReadiness(
                **base, ready=False, code=ErrorCode.SSH_IDENTITY_FILE_UNSAFE,
                configuration_ok=True, executable_ok=True,
            )

    known_exists, known_ok = _safe_regular(known_hosts_path, private=False)
    if not known_exists:
        return SSHReadiness(
            **base, ready=False, code=ErrorCode.SSH_KNOWN_HOSTS_MISSING,
            configuration_ok=True, executable_ok=True,
            key_permissions_ok=keys_ok,
        )
    if not known_ok:
        return SSHReadiness(
            **base, ready=False, code=ErrorCode.SSH_KNOWN_HOSTS_UNSAFE,
            configuration_ok=True, executable_ok=True,
            key_permissions_ok=keys_ok,
        )
    return SSHReadiness(
        **base, ready=True, code=ErrorCode.SSH_READY,
        configuration_ok=True, executable_ok=True,
        key_permissions_ok=True, known_hosts_ok=True,
    )
