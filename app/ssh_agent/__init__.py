"""Configuration foundation for the future read-only SSH agent."""

from .config import DEFAULT_CONFIG_PATH, load_config
from .models import ProjectConfig, SSHAgentConfig, ServerConfig
from .policy import CommandPolicy
from .registry import ServerRegistry
from .transport import execute
from .transport_models import ExecutionResult

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
]
