"""Configuration foundation for the future read-only SSH agent."""

from .config import DEFAULT_CONFIG_PATH, load_config
from .models import ProjectConfig, SSHAgentConfig, ServerConfig
from .policy import CommandPolicy
from .registry import ServerRegistry
from .transport import execute
from .transport_models import ExecutionResult
from .service import SSHService, parse_feature_flag, ssh_enabled_from_environment
from .service_models import SSHRequestContext, SSHServiceResult

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProjectConfig",
    "SSHAgentConfig",
    "ServerConfig",
    "ServerRegistry",
    "CommandPolicy",
    "ExecutionResult",
    "execute",
    "load_config",
    "SSHService",
    "SSHRequestContext",
    "SSHServiceResult",
    "parse_feature_flag",
    "ssh_enabled_from_environment",
]
