"""Owner-aware persistent memory service."""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
import logging
import re
from typing import Any

from app.memory.context_builder import MemoryContextBuilder
from app.memory.extractor import ExtractedMemory, MemoryExtractor
from app.memory.models import MEMORY_SCOPES, MemoryRecord, ProjectRecord
from app.memory.security import contains_secret
from app.memory.storage import MemoryStorage

logger=logging.getLogger(__name__)


class MemoryService:
    def __init__(self, storage: MemoryStorage, *, max_context_items: int=20,
                 max_context_chars: int=4000, auto_extract: bool=True) -> None:
        self.storage=storage; self.extractor=MemoryExtractor()
        self.context_builder=MemoryContextBuilder(max_context_items,max_context_chars)
        self.auto_extract_enabled=auto_extract
        storage.initialize()

    @staticmethod
    def normalize_key(value: str) -> str:
        key=re.sub(r"[^a-z0-9_.-]+","-",value.casefold()).strip("-")
        if not key or len(key)>100: raise ValueError("Invalid memory key")
        return key

    def remember(self, *, owner_id: int, scope: str, namespace: str, key: str,
                 value: Any, summary: str, source: str="user", confidence: float=1.0,
                 importance: int=5, expires_at: str | None=None) -> MemoryRecord:
        if owner_id < 0 or scope not in MEMORY_SCOPES: raise ValueError("Invalid owner or scope")
        serialized=f"{summary} {value}"
        if contains_secret(serialized):
            logger.warning("memory_skipped reason=secret owner_id=%s",owner_id)
            raise ValueError("Memory contains prohibited secret material")
        if not 0 <= confidence <= 1 or not 1 <= importance <= 10:
            raise ValueError("Invalid confidence or importance")
        namespace=self.normalize_key(namespace); key=self.normalize_key(key)
        old=self.storage.get_project(owner_id,namespace) if scope=="project" else None
        record=self.storage.upsert_memory(owner_id=owner_id,scope=scope,
            namespace=namespace,key=key,value=value,summary=summary.strip()[:500],
            source=source[:100],confidence=confidence,importance=importance,
            expires_at=expires_at,memory_type=scope,title=summary.strip()[:200],
            content=value if isinstance(value,str) else str(value),tags=(namespace,key),
            project=namespace)
        logger.info("memory_%s owner_id=%s scope=%s namespace=%s key=%s",
                    "updated" if old else "saved",owner_id,scope,namespace,key)
        return record

    def recall(self, owner_id: int, query: str="", *, namespace: str | None=None,
               limit: int=20) -> list[MemoryRecord]:
        records=self.storage.list_active(namespace,owner_id=owner_id,include_system=True)
        terms={x for x in re.findall(r"[\w-]{3,}",query.casefold())}
        if terms:
            records=[r for r in records if terms & set(re.findall(
                r"[\w-]{3,}",f"{r.namespace} {r.key} {r.summary} {r.content}".casefold()))]
        records=records[:limit]
        self.storage.mark_used((r.id for r in records),owner_id)
        logger.info("memory_recalled owner_id=%s count=%s",owner_id,len(records))
        return records

    def forget(self, owner_id: int, memory_id: int) -> bool:
        return self.storage.forget(memory_id,owner_id)

    def update_project(self, owner_id: int, project_key: str, **fields: str) -> ProjectRecord:
        return self.storage.upsert_project(owner_id,self.normalize_key(project_key),**fields)

    def record_project_event(self, owner_id: int, project_key: str, event_type: str,
                             title: str, details: Any, source: str="user",
                             deduplication_key: str | None=None):
        project=self.update_project(owner_id,project_key,name=project_key)
        dedup=deduplication_key or self.normalize_key(f"{event_type}-{title}")[:100]
        return self.storage.record_event(project.id,event_type,title,details,source,dedup)

    def get_project_context(self, owner_id: int, project_key: str) -> dict[str, Any]:
        project=self.storage.get_project(owner_id,self.normalize_key(project_key))
        memories=self.recall(owner_id,namespace=self.normalize_key(project_key))
        events=self.storage.list_events(project.id) if project else []
        return {"project":project.public_dict() if project else None,
                "memories":[m.public_dict() for m in memories],
                "events":[{"event_type":e.event_type,"title":e.title,
                           "details":e.details_json,"occurred_at":e.occurred_at}
                          for e in events]}

    def build_user_context(self, owner_id: int, query: str="") -> str:
        records=self.recall(owner_id,query,limit=self.context_builder.max_items)
        return self.context_builder.build(records,self.storage.list_projects(owner_id))

    def extract_and_remember(self, owner_id: int, text: str,
                             project_hint: str="jarvis") -> list[MemoryRecord]:
        if not self.auto_extract_enabled or contains_secret(text):
            if contains_secret(text): logger.warning("secret_redacted owner_id=%s",owner_id)
            return []
        records=[]
        for item in self.extractor.extract(text,project_hint=project_hint):
            expires=None
            if item.scope=="session_summary":
                expires=(datetime.now(timezone.utc)+timedelta(days=14)).isoformat()
            record=self.remember(owner_id=owner_id,scope=item.scope,
                namespace=item.namespace,key=item.key,value=item.value,
                summary=item.summary,source="auto_extract",confidence=item.confidence,
                importance=item.importance,expires_at=expires)
            records.append(record)
            if item.scope=="project":
                fields={"name":item.namespace}
                if item.key=="path": fields["path"]=str(item.value)
                if item.key=="next_task": fields["current_milestone"]=str(item.value)
                self.update_project(owner_id,item.namespace,**fields)
                if item.event_type:
                    self.record_project_event(owner_id,item.namespace,item.event_type,
                        item.summary,item.value,source="auto_extract",
                        deduplication_key=f"{item.event_type}-{item.key}-{str(item.value)[:40]}")
        return records
