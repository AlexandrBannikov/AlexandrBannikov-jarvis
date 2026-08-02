"""Read-only startup validation shared by the service and rollout tools."""

from app.config import Config
from app.ssh_agent.bootstrap import SSHDependencies, build_ssh_dependencies
from pathlib import Path
import importlib.util
from app.crypto_control.operations import CryptoOperationRegistry
from app.ssh_agent.transport import _validate_transport_plan


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
    if config.crypto_control_enabled:
        if not config.ssh_enabled or not dependencies.readiness.ready:
            raise RuntimeError("Crypto Control requires ready SSH Agent")
        try:
            server=dependencies.registry.get_server(config.crypto_control_host)
            project=dependencies.registry.get_project(config.crypto_control_host,"crypto-bot")
        except Exception as error:raise RuntimeError("Crypto host/project is not configured") from error
        if str(project.path)!="/opt/crypto-bot":raise RuntimeError("Crypto project path is not allowlisted")
        operations=CryptoOperationRegistry(config.crypto_control_host,config.crypto_control_timeout_seconds,config.crypto_control_max_output_bytes)
        for name in operations.names():
            period="7d" if name in {"crypto_strategy_lab","crypto_equity_history","crypto_scored_aggregate"} else None
            _validate_transport_plan(server,operations.plan(name,period=period))
    return dependencies
