"""Read-only startup validation shared by the service and rollout tools."""

from app.config import Config
from app.ssh_agent.bootstrap import SSHDependencies, build_ssh_dependencies


def startup_self_check(config: Config) -> SSHDependencies:
    """Validate the production SSH Agent source of truth without network access."""
    dependencies = build_ssh_dependencies(
        enabled=config.ssh_enabled,
        config_path=config.ssh_servers_config_path,
    )
    if config.ssh_enabled and not dependencies.readiness.ready:
        raise RuntimeError(
            f"SSH Agent readiness failed: {dependencies.readiness.code.value}"
        )
    return dependencies
