"""High-level policy manager for persistent project memory."""

from __future__ import annotations

import re

from app.memory.models import MEMORY_TYPES, MemoryRecord
from app.memory.retrieval import MemoryRetrieval
from app.memory.security import contains_secret
from app.memory.storage import MemoryStorage
from app.memory.service import MemoryService


_AUTOSAVE = re.compile(
    r"(?is)^\s*(?:запомни(?:те)?(?:,?\s+что)?|мы\s+решили(?:,?\s+что)?|"
    r"теперь\s+)(?P<content>.+?)\s*$"
)
_TAG = re.compile(r"[\w-]{3,}", re.UNICODE)


class MemoryManager:
    """Apply retention, secret and context-size policies."""

    def __init__(
        self,
        storage: MemoryStorage,
        *,
        project: str = "jarvis",
        max_results: int = 7,
        max_context: int = 4_000,
        autosave: bool = True,
        summarization: bool = True,
        summary_threshold: int = 25,
        max_context_items: int | None = None,
    ) -> None:
        self.storage = storage
        self.retrieval = MemoryRetrieval(storage)
        self.project = project
        self.max_results = max_results
        self.max_context = max_context
        self.autosave_enabled = autosave
        self.summarization_enabled = summarization
        self.summary_threshold = summary_threshold
        self.storage.initialize()
        self.service = MemoryService(
            storage,
            max_context_items=max_context_items or max_results,
            max_context_chars=max_context,
            auto_extract=autosave,
        )

    def remember(
        self,
        *,
        memory_type: str,
        title: str,
        content: str,
        tags: list[str] | tuple[str, ...],
        project: str | None = None,
        importance: int = 5,
        source: str = "user",
        owner_id: int = 0,
        confidence: float = 1.0,
        expires_at: str | None = None,
    ) -> MemoryRecord:
        self._validate(memory_type, title, content, importance)
        target_project = (project or self.project).strip()
        duplicate = self.storage.find_duplicate(
            content=content.strip(), project=target_project, owner_id=owner_id
        )
        if duplicate is not None:
            return duplicate
        record = self.storage.create(
            memory_type=memory_type,
            title=title.strip()[:200],
            content=content.strip(),
            tags=self._normalize_tags(tags),
            project=target_project,
            importance=importance,
            source=source.strip()[:100] or "user",
            owner_id=owner_id,
            confidence=confidence,
            expires_at=expires_at,
        )
        self.maybe_summarize(target_project)
        return record

    def update(
        self,
        memory_id: int,
        *,
        title: str,
        content: str,
        tags: list[str] | tuple[str, ...],
        importance: int,
        owner_id: int = 0,
    ) -> MemoryRecord:
        self._validate("note", title, content, importance)
        return self.storage.update(
            memory_id,
            title=title.strip()[:200],
            content=content.strip(),
            tags=self._normalize_tags(tags),
            importance=importance,
            owner_id=owner_id,
        )

    def forget(self, memory_id: int, *, owner_id: int = 0) -> bool:
        return self.storage.forget(memory_id, owner_id)

    def search(
        self,
        query: str,
        *,
        project: str | None = None,
        max_results: int | None = None,
        owner_id: int = 0,
    ) -> list[MemoryRecord]:
        if owner_id == 0:
            return self.retrieval.search(
                query, project=(project or self.project),
                max_results=min(max_results or self.max_results, self.max_results))
        return self.service.recall(owner_id, query, namespace=project,
                                   limit=min(max_results or self.max_results, self.max_results))

    def list_project(
        self,
        *,
        project: str | None = None,
        max_results: int | None = None,
        owner_id: int = 0,
    ) -> list[MemoryRecord]:
        if owner_id == 0:
            return self.retrieval.list_project(
                project or self.project,
                max_results=min(max_results or self.max_results, self.max_results))
        return self.service.recall(owner_id, namespace=project or self.project,
                                   limit=min(max_results or self.max_results, self.max_results))

    def relevant_context(self, query: str, *, owner_id: int = 0) -> str:
        if owner_id:
            return self.service.build_user_context(owner_id, query)
        records = self.search(query)
        if not records:
            return ""
        parts = [
            "Релевантная локальная память проекта "
            "(используй только если относится к вопросу):"
        ]
        length = len(parts[0])
        for record in records:
            item = (
                f"\n- memory_id={record.id}; type={record.memory_type}; "
                f"title={record.title}; content={record.content}"
            )
            if length + len(item) > self.max_context:
                break
            parts.append(item)
            length += len(item)
        return "".join(parts)

    def autosave(self, user_text: str, *, owner_id: int = 0) -> MemoryRecord | None:
        if owner_id:
            records = self.service.extract_and_remember(owner_id, user_text)
            return records[0] if records else None
        if not self.autosave_enabled or contains_secret(user_text):
            return None
        match = _AUTOSAVE.match(user_text)
        if not match:
            return None
        content = match.group("content").strip()
        if len(content) < 5:
            return None
        lowered = content.casefold()
        if "решили" in user_text.casefold():
            memory_type = "decision"
        elif any(word in lowered for word in ("сервер", "server", "vpn")):
            memory_type = "server"
        elif any(word in lowered for word in ("использует", "настро", "config")):
            memory_type = "configuration"
        else:
            memory_type = "fact"
        tags = [
            token.casefold()
            for token in _TAG.findall(content)
            if token.casefold()
            not in {"что", "это", "теперь", "работает", "находится"}
        ][:8]
        return self.remember(
            memory_type=memory_type,
            title=content[:80],
            content=content,
            tags=tags,
            importance=6,
            source="autosave",
        )

    def maybe_summarize(self, project: str | None = None) -> MemoryRecord | None:
        if not self.summarization_enabled:
            return None
        target = project or self.project
        records = [
            record
            for record in self.storage.list_active(target)
            if record.memory_type != "conversation_summary"
        ]
        if len(records) < self.summary_threshold:
            return None
        bucket = len(records) // self.summary_threshold
        source = f"automatic_summary:{bucket}"
        if any(
            record.source == source
            for record in self.storage.list_active(target)
        ):
            return None
        selected = sorted(
            records, key=lambda item: (item.importance, item.id), reverse=True
        )[:10]
        content = "\n".join(
            f"- {record.title}: {record.content[:300]}"
            for record in selected
        )
        return self.storage.create(
            memory_type="conversation_summary",
            title=f"Project memory summary {bucket}",
            content=content,
            tags=("summary", target),
            project=target,
            importance=7,
            source=source,
        )

    @staticmethod
    def _validate(
        memory_type: str, title: str, content: str, importance: int
    ) -> None:
        if memory_type not in MEMORY_TYPES:
            raise ValueError("Unsupported memory type")
        if not title.strip() or not content.strip():
            raise ValueError("Memory title and content are required")
        if not 1 <= importance <= 10:
            raise ValueError("Memory importance must be between 1 and 10")
        if contains_secret(title) or contains_secret(content):
            raise ValueError("Memory contains prohibited secret material")

    @staticmethod
    def _normalize_tags(tags: list[str] | tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    tag.strip().casefold()[:50]
                    for tag in tags
                    if tag.strip()
                }
            )
        )[:20]
