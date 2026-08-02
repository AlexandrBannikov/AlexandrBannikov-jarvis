"""Read-only startup validation shared by the service and rollout tools."""

from app.config import Config
from app.ssh_agent.bootstrap import SSHDependencies, build_ssh_dependencies
from pathlib import Path
import importlib.util


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
    if config.documents_enabled:
        required=("pypdf","docx","openpyxl","PIL")
        missing=[name for name in required if importlib.util.find_spec(name) is None]
        if missing: raise RuntimeError("Document dependencies missing: "+", ".join(missing))
        project=Path(__file__).resolve().parents[1]
        for path in (config.documents_storage_path,config.documents_db_path):
            try:path.resolve().relative_to(project)
            except ValueError:continue
            raise RuntimeError("Document runtime paths must be outside Git")
    return dependencies
