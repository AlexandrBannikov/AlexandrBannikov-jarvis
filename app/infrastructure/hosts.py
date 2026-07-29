"""Strict, safe loading of remote host configuration."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml

from app.infrastructure.errors import (
    HostsConfigurationError,
    UnknownHostError,
)

DEFAULT_HOSTS_CONFIG = Path("/etc/jarvis/hosts.yaml")
DEFAULT_KNOWN_HOSTS = Path("/etc/jarvis/known_hosts")
MAX_CONNECT_TIMEOUT_SECONDS = 60
MAX_COMMAND_TIMEOUT_SECONDS = 300
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9_.@-]+$")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that also rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise HostsConfigurationError("Duplicate YAML mapping key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


@dataclass(frozen=True, slots=True)
class HostConfig:
    alias: str
    hostname: str
    port: int
    username: str
    identity_file: Path
    known_hosts_file: Path
    connect_timeout_seconds: float
    command_timeout_seconds: float
    allowed_services: frozenset[str]


@dataclass(frozen=True, slots=True)
class HostsConfig:
    hosts: Mapping[str, HostConfig]

    def get(self, alias: str) -> HostConfig:
        try:
            return self.hosts[alias]
        except KeyError as error:
            raise UnknownHostError("Remote host is not configured") from error


_TOP_LEVEL_FIELDS = frozenset({"hosts", "known_hosts_file"})
_HOST_FIELDS = frozenset(
    {
        "hostname",
        "port",
        "username",
        "identity_file",
        "known_hosts_file",
        "connect_timeout_seconds",
        "command_timeout_seconds",
        "allowed_services",
    }
)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise HostsConfigurationError(f"{label} must be a mapping")
    if any(not isinstance(key, str) for key in value):
        raise HostsConfigurationError(f"{label} keys must be strings")
    return value


def _absolute_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise HostsConfigurationError(f"{label} must be a non-empty path")
    path = Path(value)
    if not path.is_absolute():
        raise HostsConfigurationError(f"{label} must be absolute")
    return path


def _timeout(value: Any, label: str, maximum: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HostsConfigurationError(f"{label} must be numeric")
    timeout = float(value)
    if timeout <= 0 or timeout > maximum:
        raise HostsConfigurationError(f"{label} is outside the allowed range")
    return timeout


def _parse_host(
    alias: str, raw: Any, default_known_hosts: Path
) -> HostConfig:
    if not alias or not ALIAS_PATTERN.fullmatch(alias):
        raise HostsConfigurationError("Host alias is invalid")
    values = _mapping(raw, f"host {alias}")
    unknown = set(values) - _HOST_FIELDS
    if unknown:
        raise HostsConfigurationError("Unknown host configuration field")

    hostname = values.get("hostname")
    if not isinstance(hostname, str) or not hostname.strip():
        raise HostsConfigurationError("hostname must not be empty")
    username = values.get("username")
    if not isinstance(username, str) or not username.strip():
        raise HostsConfigurationError("username must not be empty")
    if username.strip().lower() == "root":
        raise HostsConfigurationError("root SSH username is forbidden")

    port = values.get("port", 22)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise HostsConfigurationError("SSH port is invalid")

    services = values.get("allowed_services", [])
    if not isinstance(services, list) or any(
        not isinstance(item, str) or not SERVICE_PATTERN.fullmatch(item)
        for item in services
    ):
        raise HostsConfigurationError("allowed_services contains an invalid unit")
    if len(services) != len(set(services)):
        raise HostsConfigurationError("allowed_services contains duplicates")

    known_hosts = (
        _absolute_path(values["known_hosts_file"], "known_hosts_file")
        if "known_hosts_file" in values
        else default_known_hosts
    )
    return HostConfig(
        alias=alias,
        hostname=hostname.strip(),
        port=port,
        username=username.strip(),
        identity_file=_absolute_path(
            values.get("identity_file"), "identity_file"
        ),
        known_hosts_file=known_hosts,
        connect_timeout_seconds=_timeout(
            values.get("connect_timeout_seconds", 10),
            "connect_timeout_seconds",
            MAX_CONNECT_TIMEOUT_SECONDS,
        ),
        command_timeout_seconds=_timeout(
            values.get("command_timeout_seconds", 15),
            "command_timeout_seconds",
            MAX_COMMAND_TIMEOUT_SECONDS,
        ),
        allowed_services=frozenset(services),
    )


def load_hosts_config(
    path: str | Path = DEFAULT_HOSTS_CONFIG, *, required: bool = False
) -> HostsConfig:
    """Load a hosts YAML file without constructing arbitrary Python objects."""
    config_path = Path(path)
    if not config_path.exists():
        if required:
            raise HostsConfigurationError("Hosts configuration file is missing")
        return HostsConfig(hosts={})
    try:
        loaded = yaml.load(
            config_path.read_text(encoding="utf-8"),
            Loader=_UniqueKeyLoader,
        )
    except HostsConfigurationError:
        raise
    except (OSError, yaml.YAMLError) as error:
        raise HostsConfigurationError("Hosts configuration is invalid") from error

    root = _mapping(loaded, "configuration")
    if set(root) - _TOP_LEVEL_FIELDS:
        raise HostsConfigurationError("Unknown top-level configuration field")
    default_known_hosts = _absolute_path(
        root.get("known_hosts_file", str(DEFAULT_KNOWN_HOSTS)),
        "known_hosts_file",
    )
    raw_hosts = _mapping(root.get("hosts", {}), "hosts")
    hosts = {
        alias: _parse_host(alias, values, default_known_hosts)
        for alias, values in raw_hosts.items()
    }
    return HostsConfig(hosts=hosts)
