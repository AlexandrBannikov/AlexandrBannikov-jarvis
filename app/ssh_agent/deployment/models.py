"""Immutable deployment inventory and manifest models."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class LocalInventory:
    service_user: str
    service_group: str
    jarvis_config_dir: Path
    ssh_config_dir: Path
    servers_config_path: Path
    known_hosts_path: Path
    environment_file_path: Path


@dataclass(frozen=True, slots=True)
class ProjectInventory:
    alias: str
    display_name: str
    remote_path: Path
    allowed_services: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServerInventory:
    alias: str
    display_name: str
    host_placeholder: str
    port: int
    remote_user: str
    identity_name: str
    enabled: bool
    projects: tuple[ProjectInventory, ...]


@dataclass(frozen=True, slots=True)
class DeploymentInventory:
    version: int
    local: LocalInventory
    servers: tuple[ServerInventory, ...]


@dataclass(frozen=True, slots=True)
class DeploymentManifest:
    version: int
    local_steps: tuple[Mapping[str, object], ...]
    server_steps: tuple[Mapping[str, object], ...]
    expected_environment: Mapping[str, str]
    artifact_names: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "local_steps",
            tuple(MappingProxyType(dict(item)) for item in self.local_steps),
        )
        object.__setattr__(
            self, "server_steps",
            tuple(MappingProxyType(dict(item)) for item in self.server_steps),
        )
        object.__setattr__(
            self, "expected_environment",
            MappingProxyType(dict(self.expected_environment)),
        )
