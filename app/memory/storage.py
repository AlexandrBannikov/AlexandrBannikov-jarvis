"""Owner-aware SQLite persistence and additive migrations."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable

from app.memory.models import MemoryRecord, ProjectEvent, ProjectRecord

SCHEMA_VERSION = 2


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryStorage:
    def __init__(self, path: Path | str, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only

    def _connect(self) -> sqlite3.Connection:
        if self.read_only:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)
        else:
            db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if not self.read_only:
            db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def initialize(self) -> None:
        if self.read_only:
            raise RuntimeError("Read-only memory storage cannot initialize")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_meta "
                       "(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                memory_type TEXT NOT NULL DEFAULT 'note',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '[]',
                project TEXT NOT NULL DEFAULT 'jarvis',
                importance INTEGER NOT NULL DEFAULT 5,
                source TEXT NOT NULL DEFAULT 'migration',
                last_used_at TEXT, use_count INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                owner_id INTEGER NOT NULL DEFAULT 0,
                scope TEXT NOT NULL DEFAULT 'project',
                namespace TEXT NOT NULL DEFAULT 'jarvis',
                key TEXT NOT NULL DEFAULT '',
                value_json TEXT NOT NULL DEFAULT '{}',
                summary TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                last_accessed_at TEXT, expires_at TEXT,
                CHECK(importance BETWEEN 1 AND 10),
                CHECK(confidence BETWEEN 0.0 AND 1.0),
                CHECK(is_active IN (0,1)))""")
            self._migrate_columns(db)
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    owner_id INTEGER NOT NULL, project_key TEXT NOT NULL,
                    name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
                    repository TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                    server_name TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
                    current_milestone TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(owner_id, project_key));
                CREATE TABLE IF NOT EXISTS project_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL, title TEXT NOT NULL,
                    details_json TEXT NOT NULL DEFAULT '{}', occurred_at TEXT NOT NULL,
                    source TEXT NOT NULL, deduplication_key TEXT NOT NULL,
                    UNIQUE(project_id, deduplication_key));
                CREATE INDEX IF NOT EXISTS idx_memories_owner_scope
                    ON memories(owner_id, scope);
                CREATE INDEX IF NOT EXISTS idx_memories_lookup
                    ON memories(owner_id, scope, namespace, key);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_active_key
                    ON memories(owner_id, scope, namespace, key)
                    WHERE is_active=1 AND key<>'';
                CREATE INDEX IF NOT EXISTS idx_projects_owner ON projects(owner_id);
                CREATE INDEX IF NOT EXISTS idx_project_events_project
                    ON project_events(project_id, occurred_at);
            """)
            db.execute("UPDATE memories SET namespace=project "
                       "WHERE namespace='' OR namespace='jarvis'")
            db.execute("UPDATE memories SET summary=title WHERE summary=''")
            db.execute("UPDATE memories SET value_json=json_object('text',content) "
                       "WHERE value_json='{}' AND content<>''")
            db.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (str(SCHEMA_VERSION),))

    @staticmethod
    def _migrate_columns(db: sqlite3.Connection) -> None:
        existing = {r["name"] for r in db.execute("PRAGMA table_info(memories)")}
        additions = {
            "created_at": "TEXT NOT NULL DEFAULT ''", "updated_at": "TEXT NOT NULL DEFAULT ''",
            "memory_type": "TEXT NOT NULL DEFAULT 'note'", "title": "TEXT NOT NULL DEFAULT ''",
            "content": "TEXT NOT NULL DEFAULT ''", "tags": "TEXT NOT NULL DEFAULT '[]'",
            "project": "TEXT NOT NULL DEFAULT 'jarvis'", "importance": "INTEGER NOT NULL DEFAULT 5",
            "source": "TEXT NOT NULL DEFAULT 'migration'", "last_used_at": "TEXT",
            "use_count": "INTEGER NOT NULL DEFAULT 0", "is_active": "INTEGER NOT NULL DEFAULT 1",
            "owner_id": "INTEGER NOT NULL DEFAULT 0", "scope": "TEXT NOT NULL DEFAULT 'project'",
            "namespace": "TEXT NOT NULL DEFAULT 'jarvis'", "key": "TEXT NOT NULL DEFAULT ''",
            "value_json": "TEXT NOT NULL DEFAULT '{}'", "summary": "TEXT NOT NULL DEFAULT ''",
            "confidence": "REAL NOT NULL DEFAULT 1.0", "last_accessed_at": "TEXT",
            "expires_at": "TEXT",
        }
        for name, definition in additions.items():
            if name not in existing:
                db.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
        db.execute("UPDATE memories SET created_at=CURRENT_TIMESTAMP WHERE created_at=''")
        db.execute("UPDATE memories SET updated_at=CURRENT_TIMESTAMP WHERE updated_at=''")

    def validate_schema(self) -> bool:
        try:
            db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=10)
            db.row_factory = sqlite3.Row
            with db:
                version = db.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                cols = {r["name"] for r in db.execute("PRAGMA table_info(memories)")}
            return bool(version and int(version[0]) == SCHEMA_VERSION
                        and {"memories", "projects", "project_events"} <= tables
                        and {"owner_id", "scope", "namespace", "key", "expires_at"} <= cols)
        except (sqlite3.Error, ValueError):
            return False

    def upsert_memory(self, *, owner_id: int, scope: str, namespace: str, key: str,
                      value: Any, summary: str, source: str, confidence: float,
                      importance: int, expires_at: str | None, memory_type: str,
                      title: str, content: str, tags: Iterable[str],
                      project: str) -> MemoryRecord:
        now = utc_now_text()
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
        tags_json = json.dumps(sorted(set(tags)), ensure_ascii=False)
        with self._connect() as db:
            db.execute("""INSERT INTO memories(
                owner_id,scope,namespace,key,value_json,summary,source,confidence,
                importance,created_at,updated_at,expires_at,memory_type,title,
                content,tags,project) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id,scope,namespace,key) WHERE is_active=1 AND key<>''
                DO UPDATE SET value_json=excluded.value_json,summary=excluded.summary,
                source=excluded.source,confidence=excluded.confidence,
                importance=excluded.importance,updated_at=excluded.updated_at,
                expires_at=excluded.expires_at,memory_type=excluded.memory_type,
                title=excluded.title,content=excluded.content,tags=excluded.tags,
                project=excluded.project""",
                (owner_id, scope, namespace, key, encoded, summary, source,
                 confidence, importance, now, now, expires_at, memory_type,
                 title, content, tags_json, project))
            row = db.execute("""SELECT * FROM memories WHERE owner_id=? AND scope=?
                AND namespace=? AND key=? AND is_active=1""",
                (owner_id, scope, namespace, key)).fetchone()
        return self._memory(row)

    def create(self, *, memory_type: str, title: str, content: str,
               tags: Iterable[str], project: str, importance: int, source: str,
               owner_id: int = 0, confidence: float = 1.0,
               expires_at: str | None = None) -> MemoryRecord:
        # Legacy creates use a content hash-like stable key only for exact dedup.
        import hashlib
        key = "legacy-" + hashlib.sha256(content.encode()).hexdigest()[:24]
        return self.upsert_memory(owner_id=owner_id, scope=self.scope_for(memory_type),
            namespace=project, key=key, value={"text": content}, summary=title,
            source=source, confidence=confidence, importance=importance,
            expires_at=expires_at, memory_type=memory_type, title=title,
            content=content, tags=tags, project=project)

    @staticmethod
    def scope_for(memory_type: str) -> str:
        return {"server": "environment", "environment": "environment",
                "preference": "user_preference", "user_preference": "user_preference",
                "conversation_summary": "session_summary",
                "session_summary": "session_summary"}.get(memory_type, "project")

    def get(self, memory_id: int, owner_id: int | None = None) -> MemoryRecord | None:
        query, args = "SELECT * FROM memories WHERE id=?", [memory_id]
        if owner_id is not None:
            query += " AND owner_id IN (?,0)"
            args.append(owner_id)
        with self._connect() as db:
            row = db.execute(query, args).fetchone()
        return self._memory(row) if row else None

    def find_duplicate(self, *, content: str, project: str, owner_id: int = 0) -> MemoryRecord | None:
        with self._connect() as db:
            row = db.execute("""SELECT * FROM memories WHERE owner_id=? AND project=?
                AND content=? AND is_active=1 ORDER BY id LIMIT 1""",
                (owner_id, project, content)).fetchone()
        return self._memory(row) if row else None

    def list_active(self, project: str | None = None, *, owner_id: int = 0,
                    include_system: bool = False) -> list[MemoryRecord]:
        now = utc_now_text()
        owners = (owner_id, 0) if include_system and owner_id != 0 else (owner_id,)
        placeholders = ",".join("?" for _ in owners)
        query = f"""SELECT * FROM memories WHERE owner_id IN ({placeholders})
            AND is_active=1 AND (expires_at IS NULL OR expires_at>?)"""
        args: list[Any] = [*owners, now]
        if project is not None:
            query += " AND project=?"; args.append(project)
        query += " ORDER BY importance DESC, confidence DESC, updated_at DESC, id DESC"
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [self._memory(r) for r in rows]

    def count_active(self, owner_id: int) -> int:
        return len(self.list_active(owner_id=owner_id, include_system=True))

    def list_family_safe(self, owner_id: int) -> list[MemoryRecord]:
        """Return personal records plus explicitly shared family records only."""
        now = utc_now_text()
        with self._connect() as db:
            rows = db.execute("""SELECT * FROM memories
                WHERE is_active=1 AND (expires_at IS NULL OR expires_at>?)
                AND (owner_id=? OR (owner_id=0 AND namespace='family'))
                AND scope NOT IN ('environment','system','project')
                ORDER BY importance DESC,confidence DESC,updated_at DESC,id DESC""",
                (now, owner_id)).fetchall()
        return [self._memory(row) for row in rows]

    def update(self, memory_id: int, *, title: str, content: str,
               tags: Iterable[str], importance: int, owner_id: int = 0) -> MemoryRecord:
        with self._connect() as db:
            cur = db.execute("""UPDATE memories SET title=?,summary=?,content=?,
                value_json=?,tags=?,importance=?,updated_at=? WHERE id=?
                AND owner_id=? AND is_active=1""",
                (title, title, content, json.dumps({"text": content}, ensure_ascii=False),
                 json.dumps(sorted(set(tags)), ensure_ascii=False), importance,
                 utc_now_text(), memory_id, owner_id))
        if cur.rowcount != 1: raise KeyError("Active memory not found")
        record = self.get(memory_id, owner_id)
        assert record is not None
        return record

    def forget(self, memory_id: int, owner_id: int = 0) -> bool:
        with self._connect() as db:
            cur = db.execute("UPDATE memories SET is_active=0,updated_at=? "
                             "WHERE id=? AND owner_id=? AND is_active=1",
                             (utc_now_text(), memory_id, owner_id))
        return cur.rowcount == 1

    def mark_used(self, memory_ids: Iterable[int], owner_id: int | None = None) -> None:
        if self.read_only:
            return
        ids = list(dict.fromkeys(memory_ids))
        if not ids: return
        where = f"id IN ({','.join('?' for _ in ids)})"
        args: list[Any] = ids
        if owner_id is not None:
            where += " AND owner_id IN (?,0)"; args.extend([owner_id])
        with self._connect() as db:
            db.execute(f"""UPDATE memories SET last_used_at=?,last_accessed_at=?,
                use_count=use_count+1 WHERE {where}""",
                [utc_now_text(), utc_now_text(), *args])

    def upsert_project(self, owner_id: int, project_key: str, **fields: str) -> ProjectRecord:
        now = utc_now_text()
        values = {k: str(fields.get(k, "")) for k in
                  ("name","description","repository","path","server_name","status","current_milestone")}
        values["name"] = values["name"] or project_key
        with self._connect() as db:
            db.execute("""INSERT INTO projects(owner_id,project_key,name,description,
                repository,path,server_name,status,current_milestone,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,project_key)
                DO UPDATE SET name=CASE WHEN excluded.name<>'' THEN excluded.name ELSE name END,
                description=CASE WHEN excluded.description<>'' THEN excluded.description ELSE description END,
                repository=CASE WHEN excluded.repository<>'' THEN excluded.repository ELSE repository END,
                path=CASE WHEN excluded.path<>'' THEN excluded.path ELSE path END,
                server_name=CASE WHEN excluded.server_name<>'' THEN excluded.server_name ELSE server_name END,
                status=CASE WHEN excluded.status<>'' THEN excluded.status ELSE status END,
                current_milestone=CASE WHEN excluded.current_milestone<>'' THEN excluded.current_milestone ELSE current_milestone END,
                updated_at=excluded.updated_at""",
                (owner_id, project_key, *values.values(), now, now))
            row = db.execute("SELECT * FROM projects WHERE owner_id=? AND project_key=?",
                             (owner_id, project_key)).fetchone()
        return ProjectRecord(**dict(row))

    def get_project(self, owner_id: int, project_key: str) -> ProjectRecord | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM projects WHERE owner_id IN (?,0) "
                             "AND project_key=? ORDER BY owner_id DESC LIMIT 1",
                             (owner_id, project_key)).fetchone()
        return ProjectRecord(**dict(row)) if row else None

    def list_projects(self, owner_id: int) -> list[ProjectRecord]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM projects WHERE owner_id IN (?,0) "
                              "ORDER BY updated_at DESC", (owner_id,)).fetchall()
        return [ProjectRecord(**dict(r)) for r in rows]

    def record_event(self, project_id: int, event_type: str, title: str,
                     details: Any, source: str, deduplication_key: str,
                     occurred_at: str | None = None) -> ProjectEvent:
        with self._connect() as db:
            db.execute("""INSERT INTO project_events(project_id,event_type,title,
                details_json,occurred_at,source,deduplication_key) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(project_id,deduplication_key) DO UPDATE SET
                title=excluded.title,details_json=excluded.details_json,
                occurred_at=excluded.occurred_at,source=excluded.source""",
                (project_id,event_type,title,json.dumps(details,ensure_ascii=False),
                 occurred_at or utc_now_text(),source,deduplication_key))
            row=db.execute("SELECT * FROM project_events WHERE project_id=? AND deduplication_key=?",
                           (project_id,deduplication_key)).fetchone()
        data=dict(row); data["details_json"]=json.loads(data["details_json"])
        return ProjectEvent(**data)

    def list_events(self, project_id: int, limit: int = 20) -> list[ProjectEvent]:
        with self._connect() as db:
            rows=db.execute("SELECT * FROM project_events WHERE project_id=? "
                            "ORDER BY occurred_at DESC LIMIT ?",(project_id,limit)).fetchall()
        result=[]
        for row in rows:
            data=dict(row); data["details_json"]=json.loads(data["details_json"])
            result.append(ProjectEvent(**data))
        return result

    @staticmethod
    def _memory(row: sqlite3.Row) -> MemoryRecord:
        data=dict(row)
        try: value=json.loads(data["value_json"])
        except (json.JSONDecodeError, TypeError): value={"text": data["content"]}
        try: tags=json.loads(data["tags"])
        except (json.JSONDecodeError, TypeError): tags=[]
        return MemoryRecord(
            id=int(data["id"]), owner_id=int(data["owner_id"]), scope=data["scope"],
            namespace=data["namespace"], key=data["key"], value_json=value,
            summary=data["summary"], source=data["source"],
            confidence=float(data["confidence"]), importance=int(data["importance"]),
            created_at=data["created_at"], updated_at=data["updated_at"],
            last_accessed_at=data["last_accessed_at"], expires_at=data["expires_at"],
            is_active=bool(data["is_active"]), memory_type=data["memory_type"],
            title=data["title"], content=data["content"],
            tags=tuple(str(x) for x in tags if isinstance(x, (str,int))),
            project=data["project"], last_used_at=data["last_used_at"],
            use_count=int(data["use_count"]))
