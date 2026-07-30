"""Strict secret-free deployment inventory loader."""

from __future__ import annotations

import json
from pathlib import Path
import re

from .models import (
    DeploymentInventory, LocalInventory, ProjectInventory, ServerInventory,
)

MAX_SERVERS = 64
MAX_PROJECTS = 64
MAX_SERVICES = 128
_ALIAS = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_USER = re.compile(r"[a-z_][a-z0-9_-]{0,31}\Z")
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,252}\Z")
_IDENTITY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}_ed25519\Z")
_UNIT = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.@:-]{0,127}"
    r"\.(?:service|socket|timer|path|target|mount|automount)\Z"
)
_PATH = re.compile(r"/[A-Za-z0-9_./@+-]{1,4094}\Z")
_FORBIDDEN_KEYS = {
    "password", "token", "secret", "private_key", "private_key_content",
    "sudo_password", "command", "shell", "ssh_options", "operations",
    "environment", "argv",
}


class InventoryError(ValueError):
    code = "SSH_DEPLOYMENT_INVENTORY_INVALID"


def _exact(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise InventoryError
    if set(value) & _FORBIDDEN_KEYS:
        raise InventoryError
    return value


def _string(value: object, pattern: re.Pattern[str] | None = None,
            maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise InventoryError
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise InventoryError
    if any(char in value for char in (";", "|", "&", "`", "$", "\n", "\r")):
        raise InventoryError
    if re.search(
        r"(?i)(?:-----BEGIN|password\s*[=:]|token\s*[=:]|"
        r"private[_ -]?key\s*[=:]|api[_ -]?key\s*[=:])",
        value,
    ):
        raise InventoryError
    if pattern and pattern.fullmatch(value) is None:
        raise InventoryError
    return value


def _path(value: object) -> Path:
    text = _string(value, _PATH, 4096)
    path = Path(text)
    if not path.is_absolute() or ".." in path.parts:
        raise InventoryError
    return path


def _project(raw: object) -> ProjectInventory:
    value = _exact(raw, {"alias", "display_name", "remote_path", "allowed_services"})
    alias = _string(value["alias"], _ALIAS)
    services_raw = value["allowed_services"]
    if not isinstance(services_raw, list) or len(services_raw) > MAX_SERVICES:
        raise InventoryError
    services = tuple(_string(item, _UNIT) for item in services_raw)
    if len(set(services)) != len(services):
        raise InventoryError
    return ProjectInventory(
        alias, _string(value["display_name"], maximum=200),
        _path(value["remote_path"]), services,
    )


def _server(raw: object) -> ServerInventory:
    fields = {
        "alias", "display_name", "host_placeholder", "port", "remote_user",
        "identity_name", "enabled", "projects",
    }
    value = _exact(raw, fields)
    user = _string(value["remote_user"], _USER)
    if user == "root":
        raise InventoryError
    port = value["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise InventoryError
    enabled = value["enabled"]
    if not isinstance(enabled, bool):
        raise InventoryError
    projects_raw = value["projects"]
    if not isinstance(projects_raw, list) or len(projects_raw) > MAX_PROJECTS:
        raise InventoryError
    projects = tuple(_project(item) for item in projects_raw)
    aliases = [item.alias for item in projects]
    if len(set(aliases)) != len(aliases):
        raise InventoryError
    return ServerInventory(
        _string(value["alias"], _ALIAS),
        _string(value["display_name"], maximum=200),
        _string(value["host_placeholder"], _HOST, 253),
        port, user, _string(value["identity_name"], _IDENTITY),
        enabled, projects,
    )


def parse_inventory(raw: object) -> DeploymentInventory:
    value = _exact(raw, {"version", "local", "servers"})
    if value["version"] != 1 or isinstance(value["version"], bool):
        raise InventoryError
    local_raw = _exact(value["local"], {
        "service_user", "service_group", "jarvis_config_dir", "ssh_config_dir",
        "servers_config_path", "known_hosts_path", "environment_file_path",
    })
    local = LocalInventory(
        _string(local_raw["service_user"], _USER),
        _string(local_raw["service_group"], _USER),
        _path(local_raw["jarvis_config_dir"]),
        _path(local_raw["ssh_config_dir"]),
        _path(local_raw["servers_config_path"]),
        _path(local_raw["known_hosts_path"]),
        _path(local_raw["environment_file_path"]),
    )
    if local.known_hosts_path != Path("/etc/jarvis/ssh/known_hosts"):
        raise InventoryError
    servers_raw = value["servers"]
    if not isinstance(servers_raw, list) or not 1 <= len(servers_raw) <= MAX_SERVERS:
        raise InventoryError
    servers = tuple(_server(item) for item in servers_raw)
    aliases = [server.alias for server in servers]
    identities = [server.identity_name for server in servers]
    if len(set(aliases)) != len(aliases) or len(set(identities)) != len(identities):
        raise InventoryError
    return DeploymentInventory(1, local, servers)


def load_inventory(path: Path) -> DeploymentInventory:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise InventoryError from error
    return parse_inventory(raw)
