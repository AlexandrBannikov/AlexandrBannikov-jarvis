"""Compact grouped persistent context for the model."""

from __future__ import annotations
from app.memory.models import MemoryRecord, ProjectRecord


class MemoryContextBuilder:
    HEADINGS = {
        "user_preference": "User preferences", "environment": "Environment",
        "project": "Active projects", "session_summary": "Recent work",
        "system": "System context",
    }

    def __init__(self, max_items: int = 20, max_chars: int = 4000) -> None:
        self.max_items=max_items; self.max_chars=max_chars

    def build(self, memories: list[MemoryRecord],
              projects: list[ProjectRecord] = ()) -> str:
        if not memories and not projects:
            return ""
        lines=["Persistent context",
               "This stored context may be stale; prefer newer verified facts."]
        seen:set[str]=set(); count=0
        for scope in ("user_preference","environment","project","session_summary","system"):
            items=[m for m in memories if m.scope == scope]
            project_lines=[]
            if scope == "project":
                for p in projects:
                    fields=[f"{p.name}"]
                    if p.path: fields.append(f"path {p.path}")
                    if p.repository: fields.append(f"repository {p.repository}")
                    if p.server_name: fields.append(f"server {p.server_name}")
                    if p.current_milestone: fields.append(f"milestone {p.current_milestone}")
                    project_lines.append("; ".join(fields)+".")
            candidates=project_lines+[m.summary or m.content for m in items]
            accepted=[]
            for text in candidates:
                normalized=" ".join(text.casefold().split())
                if not text or normalized in seen or count >= self.max_items: continue
                candidate=f"- {text}"
                tentative="\n".join(lines+[self.HEADINGS[scope]+":",candidate])
                if len(tentative) > self.max_chars: break
                accepted.append(candidate); seen.add(normalized); count += 1
            if accepted:
                lines.extend(["",self.HEADINGS[scope]+":",*accepted])
        return "\n".join(lines)[:self.max_chars]
