"""Immutable typed SSH agent configuration models."""

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    alias: str
    path: Path
    services: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ServerConfig:
    alias: str
    host: str
    port: int
    user: str
    identity_file: Path
    host_key_alias: str
    enabled: bool
    projects: Mapping[str, ProjectConfig]

    def __post_init__(self) -> None:
        object.__setattr__(self, "projects", MappingProxyType(dict(self.projects)))


@dataclass(frozen=True, slots=True)
class SSHAgentConfig:
    version: int
    servers: Mapping[str, ServerConfig]

    def __post_init__(self) -> None:
        object.__setattr__(self, "servers", MappingProxyType(dict(self.servers)))
