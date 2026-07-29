"""Read-only startup validation shared by the service and rollout tools."""

from pathlib import Path
import stat

from app.config import Config
from app.infrastructure.hosts import HostsConfig, load_hosts_config


def _has_safe_mode(path: Path, maximum: int) -> bool:
    return path.exists() and stat.S_IMODE(path.stat().st_mode) & ~maximum == 0


def startup_self_check(config: Config) -> HostsConfig:
    """Validate local startup prerequisites without network access."""
    hosts = load_hosts_config(
        config.jarvis_hosts_config,
        required=config.jarvis_ssh_mode == "real",
    )
    if config.jarvis_ssh_mode == "mock":
        return hosts
    if not hosts.hosts:
        raise RuntimeError("Real SSH mode requires at least one configured host")
    for host in hosts.hosts.values():
        if host.username.lower() == "root":
            raise RuntimeError("Root SSH username is forbidden")
        if not _has_safe_mode(host.known_hosts_file, 0o640):
            raise RuntimeError("known_hosts is missing or has unsafe permissions")
        if not _has_safe_mode(host.identity_file, 0o640):
            raise RuntimeError("SSH private key is missing or has unsafe permissions")
    return hosts
