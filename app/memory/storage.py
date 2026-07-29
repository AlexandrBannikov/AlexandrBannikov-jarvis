"""SQLite storage and migrations for project memory."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Iterable

from app.memory.models import MEMORY_TYPES, MemoryRecord


SCHEMA_VERSION = 1
MEMORY_COLUMNS = frozenset(
    {
        "id",
        "created_at",
        "updated_at",
        "memory_type",
        "title",
        "content",
        "tags",
        "project",
        "importance",
        "source",
        "last_used_at",
        "use_count",
        "is_active",
    }
)


class MemoryStorage:
    """Persist memories in a local, migration-managed SQLite database."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_meta "
                "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    memory_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '[]',
                    project TEXT NOT NULL,
                    importance INTEGER NOT NULL DEFAULT 5,
                    source TEXT NOT NULL,
                    last_used_at TEXT,
                    use_count INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    CHECK (importance BETWEEN 1 AND 10),
                    CHECK (is_active IN (0, 1))
                )
                """
            )
            self._migrate_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_project_active "
                "ON memories(project, is_active)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_memories_type "
                "ON memories(memory_type)"
            )
            connection.execute(
                "INSERT INTO schema_meta(key, value) VALUES "
                "('schema_version', ?) ON CONFLICT(key) DO UPDATE SET "
                "value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @staticmethod
    def _migrate_columns(connection: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(memories)"
            ).fetchall()
        }
        additions = {
            "created_at": "TEXT NOT NULL DEFAULT ''",
            "updated_at": "TEXT NOT NULL DEFAULT ''",
            "memory_type": "TEXT NOT NULL DEFAULT 'note'",
            "title": "TEXT NOT NULL DEFAULT ''",
            "content": "TEXT NOT NULL DEFAULT ''",
            "tags": "TEXT NOT NULL DEFAULT '[]'",
            "project": "TEXT NOT NULL DEFAULT 'jarvis'",
            "importance": "INTEGER NOT NULL DEFAULT 5",
            "source": "TEXT NOT NULL DEFAULT 'migration'",
            "last_used_at": "TEXT",
            "use_count": "INTEGER NOT NULL DEFAULT 0",
            "is_active": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, definition in additions.items():
            if name not in existing:
                connection.execute(
                    f"ALTER TABLE memories ADD COLUMN {name} {definition}"
                )
        connection.execute(
            "UPDATE memories SET created_at=CURRENT_TIMESTAMP "
            "WHERE created_at=''"
        )
        connection.execute(
            "UPDATE memories SET updated_at=CURRENT_TIMESTAMP "
            "WHERE updated_at=''"
        )

    def validate_schema(self) -> bool:
        try:
            with self._connect() as connection:
                columns = {
                    row["name"]
                    for row in connection.execute(
                        "PRAGMA table_info(memories)"
                    ).fetchall()
                }
                version = connection.execute(
                    "SELECT value FROM schema_meta "
                    "WHERE key='schema_version'"
                ).fetchone()
            return (
                MEMORY_COLUMNS.issubset(columns)
                and version is not None
                and int(version["value"]) == SCHEMA_VERSION
            )
        except (sqlite3.Error, ValueError, KeyError):
            return False

    def create(
        self,
        *,
        memory_type: str,
        title: str,
        content: str,
        tags: Iterable[str],
        project: str,
        importance: int,
        source: str,
    ) -> MemoryRecord:
        if memory_type not in MEMORY_TYPES:
            raise ValueError("Unsupported memory type")
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO memories(
                    memory_type, title, content, tags, project,
                    importance, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory_type,
                    title,
                    content,
                    json.dumps(sorted(set(tags)), ensure_ascii=False),
                    project,
                    importance,
                    source,
                ),
            )
            memory_id = int(cursor.lastrowid)
        record = self.get(memory_id)
        if record is None:
            raise RuntimeError("Memory insert failed")
        return record

    def get(self, memory_id: int) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE id=?", (memory_id,)
            ).fetchone()
        return self._record(row) if row else None

    def find_duplicate(
        self, *, content: str, project: str
    ) -> MemoryRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM memories WHERE project=? AND content=? "
                "AND is_active=1 ORDER BY id LIMIT 1",
                (project, content),
            ).fetchone()
        return self._record(row) if row else None

    def list_active(self, project: str | None = None) -> list[MemoryRecord]:
        query = "SELECT * FROM memories WHERE is_active=1"
        arguments: tuple[object, ...] = ()
        if project is not None:
            query += " AND project=?"
            arguments = (project,)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, arguments).fetchall()
        return [self._record(row) for row in rows]

    def update(
        self,
        memory_id: int,
        *,
        title: str,
        content: str,
        tags: Iterable[str],
        importance: int,
    ) -> MemoryRecord:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE memories SET title=?, content=?, tags=?,
                    importance=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND is_active=1
                """,
                (
                    title,
                    content,
                    json.dumps(sorted(set(tags)), ensure_ascii=False),
                    importance,
                    memory_id,
                ),
            )
        if cursor.rowcount != 1:
            raise KeyError("Active memory not found")
        record = self.get(memory_id)
        if record is None:
            raise RuntimeError("Memory update failed")
        return record

    def forget(self, memory_id: int) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE memories SET is_active=0, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=? AND is_active=1",
                (memory_id,),
            )
        return cursor.rowcount == 1

    def mark_used(self, memory_ids: Iterable[int]) -> None:
        ids = list(dict.fromkeys(memory_ids))
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE memories SET last_used_at=CURRENT_TIMESTAMP, "
                f"use_count=use_count+1 WHERE id IN ({placeholders})",
                ids,
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @staticmethod
    def _record(row: sqlite3.Row) -> MemoryRecord:
        tags = json.loads(row["tags"])
        if not isinstance(tags, list):
            tags = []
        return MemoryRecord(
            id=int(row["id"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            memory_type=str(row["memory_type"]),
            title=str(row["title"]),
            content=str(row["content"]),
            tags=tuple(str(tag) for tag in tags),
            project=str(row["project"]),
            importance=int(row["importance"]),
            source=str(row["source"]),
            last_used_at=(
                str(row["last_used_at"])
                if row["last_used_at"] is not None
                else None
            ),
            use_count=int(row["use_count"]),
            is_active=bool(row["is_active"]),
        )
