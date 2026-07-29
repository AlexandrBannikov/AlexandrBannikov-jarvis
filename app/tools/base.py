"""Base interface for all Jarvis tools."""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """A named, self-describing operation available to Jarvis."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the unique tool name."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Return a short human-readable tool description."""

    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """Return the tool's JSON-compatible parameter schema."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> dict[str, Any]:
        """Execute the tool and return JSON-compatible data."""
