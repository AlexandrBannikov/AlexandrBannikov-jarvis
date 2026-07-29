"""Bounded retrieval over local project memories."""

from __future__ import annotations

from app.memory.models import MemoryRecord
from app.memory.ranking import rank_memory, terms
from app.memory.storage import MemoryStorage


class MemoryRetrieval:
    """Find and rank relevant records without embeddings."""

    def __init__(self, storage: MemoryStorage) -> None:
        self.storage = storage

    def search(
        self,
        query: str,
        *,
        project: str,
        max_results: int,
    ) -> list[MemoryRecord]:
        query_terms = terms(query)
        candidates = self.storage.list_active(project)
        ranked = [
            (rank_memory(record, query), record)
            for record in candidates
            if not query_terms
            or query_terms
            & (
                terms(record.title)
                | terms(record.content)
                | {tag.casefold() for tag in record.tags}
            )
        ]
        ranked.sort(key=lambda item: (item[0], item[1].id), reverse=True)
        selected = [
            record for score, record in ranked[:max_results] if score > 0
        ]
        self.storage.mark_used(record.id for record in selected)
        return selected

    def list_project(
        self, project: str, *, max_results: int
    ) -> list[MemoryRecord]:
        selected = self.storage.list_active(project)[:max_results]
        self.storage.mark_used(record.id for record in selected)
        return selected
