"""SQLite persistence and atomic leasing for reminders."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.reminders.models import Reminder

SCHEMA_VERSION = 1


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("Timezone-aware datetime required")
    return value.astimezone(timezone.utc).isoformat()


class ReminderStorage:
    COLUMNS = tuple(Reminder.__dataclass_fields__)

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    component TEXT PRIMARY KEY,
                    version INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    message TEXT NOT NULL,
                    reminder_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    scheduled_at_utc TEXT NULL,
                    next_run_at_utc TEXT NULL,
                    recurrence_rule TEXT NULL,
                    last_run_at_utc TEXT NULL,
                    completed_at TEXT NULL,
                    cancelled_at TEXT NULL,
                    paused_at TEXT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    delivery_attempts INTEGER NOT NULL DEFAULT 0,
                    last_delivery_error_code TEXT NULL,
                    source_message_id INTEGER NULL,
                    deduplication_key TEXT NULL,
                    lease_owner TEXT NULL,
                    lease_until_utc TEXT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    CHECK(reminder_type IN ('one_time','recurring')),
                    CHECK(status IN ('scheduled','running','completed','cancelled','failed','paused'))
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_user ON reminders(user_id);
                CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
                CREATE INDEX IF NOT EXISTS idx_reminders_next_run ON reminders(next_run_at_utc);
                CREATE INDEX IF NOT EXISTS idx_reminders_dedup ON reminders(deduplication_key);
                CREATE INDEX IF NOT EXISTS idx_reminders_source ON reminders(source_message_id);
                CREATE INDEX IF NOT EXISTS idx_reminders_lease ON reminders(lease_until_utc);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_source_unique
                    ON reminders(user_id, chat_id, source_message_id)
                    WHERE source_message_id IS NOT NULL;
                INSERT INTO schema_meta(component, version)
                    VALUES ('reminders', 1)
                    ON CONFLICT(component) DO UPDATE SET version=excluded.version;
                """
            )

    def validate_schema(self) -> bool:
        try:
            with self.connect() as db:
                version = db.execute(
                    "SELECT version FROM schema_meta WHERE component='reminders'"
                ).fetchone()
                columns = {
                    row["name"] for row in db.execute("PRAGMA table_info(reminders)")
                }
            return version is not None and version[0] == SCHEMA_VERSION and set(
                self.COLUMNS
            ) <= columns
        except sqlite3.Error:
            return False

    def create(self, values: dict[str, object]) -> tuple[Reminder, bool]:
        now = utc_text(utc_now())
        fields = {
            **values,
            "status": "scheduled",
            "created_at": now,
            "updated_at": now,
            "delivery_attempts": 0,
            "is_active": 1,
        }
        columns = list(fields)
        placeholders = ",".join("?" for _ in columns)
        try:
            with self.connect() as db:
                cursor = db.execute(
                    f"INSERT INTO reminders ({','.join(columns)}) VALUES ({placeholders})",
                    [fields[name] for name in columns],
                )
                row = db.execute(
                    "SELECT * FROM reminders WHERE id=?", (cursor.lastrowid,)
                ).fetchone()
            return self._record(row), True
        except sqlite3.IntegrityError:
            source_id = values.get("source_message_id")
            if source_id is None:
                raise
            existing = self.by_source(
                int(values["user_id"]), int(values["chat_id"]), int(source_id)
            )
            if existing is None:
                raise
            return existing, False

    def by_source(self, user_id: int, chat_id: int, source_message_id: int) -> Reminder | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM reminders WHERE user_id=? AND chat_id=? AND source_message_id=?",
                (user_id, chat_id, source_message_id),
            ).fetchone()
        return self._record(row) if row else None

    def get(self, reminder_id: int, user_id: int) -> Reminder | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM reminders WHERE id=? AND user_id=?",
                (reminder_id, user_id),
            ).fetchone()
        return self._record(row) if row else None

    def list_user(self, user_id: int, limit: int = 20, include_completed: bool = False) -> list[Reminder]:
        where = "user_id=?"
        if not include_completed:
            where += " AND status IN ('scheduled','running','paused') AND is_active=1"
        with self.connect() as db:
            rows = db.execute(
                f"SELECT * FROM reminders WHERE {where} "
                "ORDER BY CASE status WHEN 'paused' THEN 1 ELSE 0 END, next_run_at_utc LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._record(row) for row in rows]

    def active_count(self, user_id: int | None = None) -> int:
        query = "SELECT COUNT(*) FROM reminders WHERE is_active=1 AND status IN ('scheduled','running','paused')"
        params: tuple[object, ...] = ()
        if user_id is not None:
            query += " AND user_id=?"
            params = (user_id,)
        with self.connect() as db:
            return int(db.execute(query, params).fetchone()[0])

    def find(self, user_id: int, reference: str, limit: int = 5) -> list[Reminder]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM reminders WHERE user_id=? AND is_active=1 "
                "AND status IN ('scheduled','paused') "
                "ORDER BY next_run_at_utc LIMIT 100",
                (user_id,),
            ).fetchall()
        needle = reference.casefold().strip()
        return [
            record
            for record in (self._record(row) for row in rows)
            if needle in record.title.casefold() or needle in record.message.casefold()
        ][:limit]

    def update_owned(self, reminder_id: int, user_id: int, fields: dict[str, object]) -> Reminder | None:
        allowed = {
            "title", "message", "timezone", "scheduled_at_utc", "next_run_at_utc",
            "recurrence_rule", "status", "completed_at", "cancelled_at", "paused_at",
            "delivery_attempts", "last_delivery_error_code", "deduplication_key",
            "lease_owner", "lease_until_utc", "last_run_at_utc", "is_active",
        }
        if not fields or not set(fields) <= allowed:
            raise ValueError("Invalid update fields")
        fields = {**fields, "updated_at": utc_text(utc_now())}
        assignments = ",".join(f"{name}=?" for name in fields)
        with self.connect() as db:
            cursor = db.execute(
                f"UPDATE reminders SET {assignments} WHERE id=? AND user_id=?",
                [*fields.values(), reminder_id, user_id],
            )
            if cursor.rowcount != 1:
                return None
            row = db.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
        return self._record(row)

    def claim_due(
        self, now: datetime, *, owner: str, lease_seconds: int, limit: int = 20
    ) -> list[Reminder]:
        now_text = utc_text(now)
        lease_until = utc_text(now + timedelta(seconds=lease_seconds))
        claimed: list[Reminder] = []
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            ids = [
                row[0] for row in db.execute(
                    "SELECT id FROM reminders WHERE is_active=1 AND status IN ('scheduled','running') "
                    "AND next_run_at_utc<=? AND (lease_until_utc IS NULL OR lease_until_utc<?) "
                    "ORDER BY next_run_at_utc LIMIT ?",
                    (now_text, now_text, limit),
                ).fetchall()
            ]
            for reminder_id in ids:
                cursor = db.execute(
                    "UPDATE reminders SET status='running',lease_owner=?,lease_until_utc=?,updated_at=? "
                    "WHERE id=? AND (lease_until_utc IS NULL OR lease_until_utc<?)",
                    (owner, lease_until, now_text, reminder_id, now_text),
                )
                if cursor.rowcount == 1:
                    row = db.execute("SELECT * FROM reminders WHERE id=?", (reminder_id,)).fetchone()
                    claimed.append(self._record(row))
        return claimed

    def metrics(self, now: datetime | None = None) -> dict[str, int]:
        current = utc_text(now or utc_now())
        with self.connect() as db:
            active = db.execute(
                "SELECT COUNT(*) FROM reminders WHERE is_active=1 AND status IN ('scheduled','running','paused')"
            ).fetchone()[0]
            due = db.execute(
                "SELECT COUNT(*) FROM reminders WHERE is_active=1 AND status='scheduled' AND next_run_at_utc<=?",
                (current,),
            ).fetchone()[0]
            failed = db.execute("SELECT COUNT(*) FROM reminders WHERE status='failed'").fetchone()[0]
        return {"active": int(active), "due": int(due), "failed": int(failed)}

    @staticmethod
    def _record(row: sqlite3.Row) -> Reminder:
        values = dict(row)
        values["is_active"] = bool(values["is_active"])
        return Reminder(**values)
