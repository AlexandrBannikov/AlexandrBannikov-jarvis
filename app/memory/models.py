"""Data models for local project memory."""

from __future__ import annotations

from dataclasses import dataclass


MEMORY_TYPES = frozenset(
    {
        "project",
        "server",
        "decision",
        "fact",
        "preference",
        "todo",
        "note",
        "configuration",
        "conversation_summary",
    }
)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    id: int
    created_at: str
    updated_at: str
    memory_type: str
    title: str
    content: str
    tags: tuple[str, ...]
    project: str
    importance: int
    source: str
    last_used_at: str | None
    use_count: int
    is_active: bool

    def public_dict(self) -> dict[str, object]:
        """Return fields safe and useful to the model."""
        return {
            "id": self.id,
            "memory_type": self.memory_type,
            "title": self.title,
            "content": self.content,
            "tags": list(self.tags),
            "project": self.project,
            "importance": self.importance,
            "updated_at": self.updated_at,
        }
