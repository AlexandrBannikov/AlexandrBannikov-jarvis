"""Exact, read-only lookup over trusted SSH configuration."""

from .errors import (
    ProjectNotFoundError,
    ServerDisabledError,
    ServerNotFoundError,
    ServiceNotAllowedError,
)
from .models import ProjectConfig, SSHAgentConfig, ServerConfig


class ServerRegistry:
    def __init__(self, config: SSHAgentConfig) -> None:
        self._servers = dict(config.servers)

    def list_servers(self, include_disabled: bool = False) -> tuple[ServerConfig, ...]:
        return tuple(
            server
            for server in self._servers.values()
            if include_disabled or server.enabled
        )

    def get_server(self, alias: str, require_enabled: bool = True) -> ServerConfig:
        server = self._servers.get(alias)
        if server is None:
            raise ServerNotFoundError()
        if require_enabled and not server.enabled:
            raise ServerDisabledError()
        return server

    def list_projects(
        self, server_alias: str, include_disabled_server: bool = False
    ) -> tuple[ProjectConfig, ...]:
        server = self.get_server(
            server_alias, require_enabled=not include_disabled_server
        )
        return tuple(server.projects.values())

    def get_project(self, server_alias: str, project_alias: str) -> ProjectConfig:
        server = self.get_server(server_alias)
        project = server.projects.get(project_alias)
        if project is None:
            raise ProjectNotFoundError()
        return project

    def list_services(self, server_alias: str, project_alias: str) -> tuple[str, ...]:
        return self.get_project(server_alias, project_alias).services

    def service_allowed(
        self, server_alias: str, project_alias: str, service_name: str
    ) -> bool:
        services = self.list_services(server_alias, project_alias)
        if service_name not in services:
            raise ServiceNotAllowedError()
        return True
