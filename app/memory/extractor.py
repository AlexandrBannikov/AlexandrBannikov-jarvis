"""Conservative, deterministic durable-fact extraction."""

from __future__ import annotations
from dataclasses import dataclass
import re

from app.memory.security import contains_secret


@dataclass(frozen=True, slots=True)
class ExtractedMemory:
    scope: str
    namespace: str
    key: str
    value: object
    summary: str
    importance: int = 7
    confidence: float = .9
    event_type: str | None = None


class MemoryExtractor:
    _explicit = re.compile(r"(?is)^\s*(?:запомни(?:те)?(?:,?\s+что)?|"
                           r"мы\s+решили(?:,?\s+что)?|теперь\s+)(.+?)\s*$")
    _path = re.compile(r"(?i)([\w-]+(?:\s+[\w-]+){0,2}?(?:\s+bot)?|jarvis)"
                       r".{0,40}?(?:находится|path|путь)\s*(?:в|:|=)?\s*(/[^\s,;]+)")
    _tests = re.compile(r"\b(\d{1,7})\s+passed\b", re.I)
    _commit = re.compile(r"\bcommit\s+([0-9a-f]{7,40})\b", re.I)
    _next = re.compile(r"(?i)(?:следующ(?:ий|ая)\s+(?:этап|задача)|"
                       r"next\s+(?:step|milestone))\s*(?:—|-|:|=)\s*(.+)")
    _server = re.compile(r"(?i)(?:сервер(?:\s+разработки)?\s+(?:называется|—|-|:)|"
                         r"server\s*(?:name)?\s*[:=])\s*([\w.-]+)")

    def extract(self, text: str, *, project_hint: str = "jarvis") -> list[ExtractedMemory]:
        if not text.strip() or contains_secret(text):
            return []
        result: list[ExtractedMemory] = []
        path = self._path.search(text)
        if path:
            project = self._project_key(path.group(1))
            result.append(ExtractedMemory("project", project, "path", path.group(2),
                                         f"Project path: {path.group(2)}", 9, .98))
        for match in self._tests.finditer(text):
            result.append(ExtractedMemory("project", project_hint, "latest_tests",
                {"passed": int(match.group(1)), "text": match.group(0)},
                f"Latest known tests: {match.group(0)}", 8, .98, "tests"))
        for match in self._commit.finditer(text):
            result.append(ExtractedMemory("project", project_hint, "latest_commit",
                match.group(1).lower(), f"Latest known commit: {match.group(1).lower()}",
                9, .99, "commit"))
        next_step = self._next.search(text)
        if next_step:
            value=next_step.group(1).strip(" .")
            result.append(ExtractedMemory("project", project_hint, "next_task", value,
                f"Next task: {value}", 8, .95, "next_task"))
        server = self._server.search(text)
        if server:
            result.append(ExtractedMemory("environment", "server", "development_server",
                server.group(1), f"Development server: {server.group(1)}", 9, .98))
        lowered=text.casefold()
        if re.search(r"не (?:делай|выполня(?:й|ть)).{0,30}\bcommit\b.{0,20}\bpush\b", lowered):
            value="Do not commit or push without explicit permission."
            result.append(ExtractedMemory("user_preference","workflow","commit_push_permission",
                                          value,value,10,.99))
        if "работаем через codex" in lowered or "development through codex" in lowered:
            result.append(ExtractedMemory("user_preference","workflow","development_tool",
                                          "Development is performed through Codex.",
                                          "Development is performed through Codex.",8,.98))
        explicit=self._explicit.match(text)
        if explicit and not result:
            value=explicit.group(1).strip()
            result.append(ExtractedMemory("project",project_hint,
                "explicit-"+re.sub(r"\W+","-",value.casefold()).strip("-")[:48],
                value,value,6,.85))
        # Normalize common status lines, but never arbitrary terminal output.
        if re.search(r"(?i)\bgit push completed\b", text):
            result.append(ExtractedMemory("project",project_hint,"last_push","completed",
                                          "Last known git push: completed",7,.95,"push"))
        if re.search(r"(?i)\bworking tree clean\b", text):
            result.append(ExtractedMemory("session_summary",project_hint,"working_tree","clean",
                                          "Working tree was clean.",5,.95,"git_status"))
        if re.search(r"(?i)\bproduction (?:was )?not changed\b", text):
            result.append(ExtractedMemory("session_summary",project_hint,"production_changed",
                                          False,"Production was not changed.",8,.98,
                                          "production"))
        return self._dedup(result)

    @staticmethod
    def _project_key(value: str) -> str:
        return re.sub(r"[^a-z0-9]+","-",value.casefold()).strip("-") or "project"

    @staticmethod
    def _dedup(items: list[ExtractedMemory]) -> list[ExtractedMemory]:
        return list({(x.scope,x.namespace,x.key): x for x in items}.values())
