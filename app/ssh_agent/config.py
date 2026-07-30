"""Strict JSON loader for the SSH server registry."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import re
import stat

from .errors import ConfigError, ErrorCode
from .models import ProjectConfig, SSHAgentConfig, ServerConfig

DEFAULT_CONFIG_PATH = Path("/etc/jarvis/servers.json")
CONFIG_ENV_VAR = "JARVIS_SERVERS_CONFIG"
SUPPORTED_VERSION = 1
MAX_SERVERS = 64
MAX_PROJECTS = 64
MAX_SERVICES = 128
MAX_SIMPLE_LENGTH = 128
MAX_PATH_LENGTH = 4096
MAX_HOST_LENGTH = 253

_ALIAS_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_USER_RE = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_HOST_ALIAS_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}\Z")
_UNIT_RE = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}"
    r"\.(?:service|socket|timer|path|target|mount|automount)\Z"
)
_PATH_RE = re.compile(r"/[A-Za-z0-9_./@+-]{0,4094}[A-Za-z0-9_./@+-]\Z")
_SECRET_FIELDS = frozenset(
    {
        "private_key",
        "private_key_data",
        "password",
        "passphrase",
        "token",
        "api_key",
        "secret",
        "env",
        "command",
        "shell",
        "sudo",
    }
)
_SECRET_MARKERS = ("password", "passphrase", "private_key", "token", "api_key", "secret")


class _ObjectPairs(list[tuple[str, object]]):
    """Preserve JSON object pairs so duplicate keys cannot disappear."""


def _schema_error() -> ConfigError:
    return ConfigError(ErrorCode.CONFIG_INVALID_SCHEMA)


def _unsafe_error() -> ConfigError:
    return ConfigError(ErrorCode.CONFIG_UNSAFE)


def _object(value: object, allowed: frozenset[str]) -> dict[str, object]:
    if not isinstance(value, _ObjectPairs):
        raise _schema_error()
    result: dict[str, object] = {}
    for key, item in value:
        if not isinstance(key, str):
            raise _schema_error()
        normalized_key = key.lower()
        if key in _SECRET_FIELDS or any(
            marker in normalized_key for marker in _SECRET_MARKERS
        ):
            raise _unsafe_error()
        if key not in allowed or key in result:
            raise _schema_error()
        result[key] = item
    return result


def _required_fields(value: dict[str, object], required: frozenset[str]) -> None:
    if value.keys() != required:
        raise _schema_error()


def _safe_string(
    value: object,
    *,
    maximum: int = MAX_SIMPLE_LENGTH,
    pattern: re.Pattern[str] | None = None,
) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise _schema_error()
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise _unsafe_error()
    if pattern is not None and pattern.fullmatch(value) is None:
        raise _schema_error()
    return value


def _path(value: object) -> Path:
    text = _safe_string(value, maximum=MAX_PATH_LENGTH, pattern=_PATH_RE)
    path = Path(text)
    if not path.is_absolute():
        raise _schema_error()
    return path


def _parse_project(alias: str, raw: object) -> ProjectConfig:
    value = _object(raw, frozenset({"path", "services"}))
    _required_fields(value, frozenset({"path", "services"}))
    services_raw = value["services"]
    if not isinstance(services_raw, list) or isinstance(services_raw, _ObjectPairs):
        raise _schema_error()
    if len(services_raw) > MAX_SERVICES:
        raise _schema_error()
    services: list[str] = []
    seen: set[str] = set()
    for raw_service in services_raw:
        service = _safe_string(raw_service, pattern=_UNIT_RE)
        if service in seen:
            raise _schema_error()
        seen.add(service)
        services.append(service)
    return ProjectConfig(alias=alias, path=_path(value["path"]), services=tuple(services))


def _parse_server(alias: str, raw: object) -> ServerConfig:
    fields = frozenset(
        {
            "host",
            "port",
            "user",
            "identity_file",
            "host_key_alias",
            "enabled",
            "projects",
        }
    )
    value = _object(raw, fields)
    _required_fields(value, fields)
    port = value["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise _schema_error()
    enabled = value["enabled"]
    if not isinstance(enabled, bool):
        raise _schema_error()
    projects_raw = value["projects"]
    if not isinstance(projects_raw, _ObjectPairs):
        raise _schema_error()
    if len(projects_raw) > MAX_PROJECTS:
        raise _schema_error()
    projects: dict[str, ProjectConfig] = {}
    for project_alias_raw, project_raw in projects_raw:
        project_alias = _safe_string(project_alias_raw, pattern=_ALIAS_RE)
        normalized = project_alias.lower()
        if normalized in projects:
            raise _schema_error()
        projects[normalized] = _parse_project(project_alias, project_raw)
    return ServerConfig(
        alias=alias,
        host=_safe_string(value["host"], maximum=MAX_HOST_LENGTH, pattern=_HOST_RE),
        port=port,
        user=_safe_string(value["user"], pattern=_USER_RE),
        identity_file=_path(value["identity_file"]),
        host_key_alias=_safe_string(value["host_key_alias"], pattern=_HOST_ALIAS_RE),
        enabled=enabled,
        projects=projects,
    )


def _parse(raw: object) -> SSHAgentConfig:
    root = _object(raw, frozenset({"version", "servers"}))
    _required_fields(root, frozenset({"version", "servers"}))
    if root["version"] != SUPPORTED_VERSION or isinstance(root["version"], bool):
        raise _schema_error()
    servers_raw = root["servers"]
    if not isinstance(servers_raw, _ObjectPairs):
        raise _schema_error()
    if not 1 <= len(servers_raw) <= MAX_SERVERS:
        raise _schema_error()
    servers: dict[str, ServerConfig] = {}
    for alias_raw, server_raw in servers_raw:
        alias = _safe_string(alias_raw, pattern=_ALIAS_RE)
        normalized = alias.lower()
        if normalized in servers:
            raise _schema_error()
        servers[normalized] = _parse_server(alias, server_raw)
    return SSHAgentConfig(version=SUPPORTED_VERSION, servers=servers)


def _validate_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except FileNotFoundError as error:
        raise ConfigError(ErrorCode.CONFIG_NOT_FOUND) from error
    except OSError as error:
        raise ConfigError(ErrorCode.CONFIG_UNSAFE) from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _unsafe_error()
    if metadata.st_uid != os.geteuid():
        raise _unsafe_error()
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise _unsafe_error()


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    validate_permissions: bool = True,
) -> SSHAgentConfig:
    """Load a strict config from an explicit path or the environment/default."""
    values = os.environ if environment is None else environment
    selected = Path(path) if path is not None else Path(
        values.get(CONFIG_ENV_VAR, str(DEFAULT_CONFIG_PATH))
    )
    if validate_permissions:
        _validate_file(selected)
    try:
        text = selected.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ConfigError(ErrorCode.CONFIG_NOT_FOUND) from error
    except (OSError, UnicodeError) as error:
        raise ConfigError(ErrorCode.CONFIG_UNSAFE) from error
    if re.search(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----", text):
        raise _unsafe_error()
    try:
        raw = json.loads(text, object_pairs_hook=_ObjectPairs)
    except (json.JSONDecodeError, RecursionError) as error:
        raise ConfigError(ErrorCode.CONFIG_INVALID_JSON) from error
    return _parse(raw)
