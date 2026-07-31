"""Models used by the built-in Skills Registry."""

from dataclasses import dataclass, field
from enum import StrEnum


class HealthStatus(StrEnum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    skill_id: str
    name: str
    version: str
    description: str
    category: str
    enabled: bool
    required: bool
    source: str
    tool_names: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillReport:
    metadata: SkillMetadata
    health_status: HealthStatus
    health_message: str
    dependency_status: tuple[tuple[str, HealthStatus], ...] = field(default_factory=tuple)

    @property
    def skill_id(self) -> str:
        return self.metadata.skill_id
