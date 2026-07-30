from pathlib import Path

import pytest

from app.ssh_agent.errors import (
    ProjectNotFoundError,
    ServerDisabledError,
    ServerNotFoundError,
    ServiceNotAllowedError,
)
from app.ssh_agent.models import ProjectConfig, SSHAgentConfig, ServerConfig
from app.ssh_agent.registry import ServerRegistry


def registry() -> ServerRegistry:
    project = ProjectConfig("app", Path("/opt/app"), ("app.service", "app.timer"))
    enabled = ServerConfig(
        "alpha",
        "localhost",
        22,
        "jarvis-ops",
        Path("/keys/id"),
        "alpha",
        True,
        {"app": project},
    )
    disabled = ServerConfig(
        "beta",
        "localhost",
        22,
        "jarvis-ops",
        Path("/keys/id"),
        "beta",
        False,
        {},
    )
    return ServerRegistry(SSHAgentConfig(1, {"alpha": enabled, "beta": disabled}))


def test_list_servers_filters_disabled() -> None:
    assert tuple(item.alias for item in registry().list_servers()) == ("alpha",)
    assert tuple(item.alias for item in registry().list_servers(True)) == ("alpha", "beta")


def test_get_known_unknown_and_disabled_server() -> None:
    assert registry().get_server("alpha").alias == "alpha"
    with pytest.raises(ServerNotFoundError):
        registry().get_server("missing")
    with pytest.raises(ServerDisabledError):
        registry().get_server("beta")
    assert registry().get_server("beta", require_enabled=False).alias == "beta"


def test_projects_and_services_are_exact_allowlists() -> None:
    value = registry()
    assert tuple(project.alias for project in value.list_projects("alpha")) == ("app",)
    assert value.get_project("alpha", "app").path == Path("/opt/app")
    assert value.list_services("alpha", "app") == ("app.service", "app.timer")
    assert value.service_allowed("alpha", "app", "app.service")
    with pytest.raises(ProjectNotFoundError):
        value.get_project("alpha", "missing")
    with pytest.raises(ServiceNotAllowedError):
        value.service_allowed("alpha", "app", "app")
    with pytest.raises(ServiceNotAllowedError):
        value.service_allowed("alpha", "app", "foreign.service")


def test_returned_structures_cannot_mutate_registry() -> None:
    value = registry()
    servers = value.list_servers()
    with pytest.raises(AttributeError):
        servers.append(servers[0])  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        servers[0].projects["new"] = servers[0].projects["app"]  # type: ignore[index]
    assert tuple(project.alias for project in value.list_projects("alpha")) == ("app",)
