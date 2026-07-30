"""Configuration foundation for the future read-only SSH agent."""

from .config import DEFAULT_CONFIG_PATH, load_config
from .models import ProjectConfig, SSHAgentConfig, ServerConfig
from .registry import ServerRegistry

__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ProjectConfig",
    "SSHAgentConfig",
    "ServerConfig",
    "ServerRegistry",
    "load_config",
]
