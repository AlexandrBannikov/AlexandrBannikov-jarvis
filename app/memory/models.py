"""Typed records used by persistent memory."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MEMORY_TYPES = frozenset(
    {
        "environment", "project", "user_preference", "session_summary",
        # Backward-compatible aliases.
        "server", "decision", "fact", "preference", "todo", "note",
        "configuration", "conversation_summary",
    }
)
MEMORY_SCOPES = frozenset(
    {"environment", "project", "user_preference", "session_summary", "system"}
)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    owner_id: int
    scope: str
    namespace: str
    key: str
    value_json: Any
    summary: str
    source: str
    confidence: float
    importance: int
    created_at: str
    updated_at: str
    last_accessed_at: str | None
    expires_at: str | None
    is_active: bool
    # Legacy public fields.
    memory_type: str
    title: str
    content: str
    tags: tuple[str, ...]
    project: str
    last_used_at: str | None
    use_count: int

    def public_dict(self) -> dict[str, object]:
        return {
            "id": self.id, "scope": self.scope, "namespace": self.namespace,
            "key": self.key, "summary": self.summary,
            "value": self.value_json, "confidence": self.confidence,
            "importance": self.importance, "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "memory_type": self.memory_type, "title": self.title,
            "content": self.content, "tags": list(self.tags),
            "project": self.project,
        }


@dataclass(frozen=True, slots=True)
class ProjectRecord:
    id: int
    owner_id: int
    project_key: str
    name: str
    description: str
    repository: str
    path: str
    server_name: str
    status: str
    current_milestone: str
    created_at: str
    updated_at: str

    def public_dict(self) -> dict[str, object]:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}


@dataclass(frozen=True, slots=True)
class ProjectEvent:
    id: int
    project_id: int
    event_type: str
    title: str
    details_json: Any
    occurred_at: str
    source: str
    deduplication_key: str
