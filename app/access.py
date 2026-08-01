"""Trusted role, capability and one-time family onboarding storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import logging
from pathlib import Path
import secrets
import sqlite3
import time
from collections import defaultdict, deque

audit_logger = logging.getLogger("jarvis.audit")

OWNER = "owner"
FAMILY_USER = "family_user"
ACTIVE = "active"
VALID_ROLES = frozenset({OWNER, FAMILY_USER})

CAPABILITIES = frozenset({
    "assistant.chat", "assistant.web_search", "assistant.weather",
    "memory.personal.read", "memory.personal.write",
    "memory.family.read", "memory.family.write",
    "reminders.personal.read", "reminders.personal.write",
    "location.personal.read", "location.personal.write",
    "timezone.personal.read", "conversation.personal", "tools.general",
    "technical.ssh", "technical.systemd", "technical.logs",
    "technical.server_health", "technical.production_diagnostics",
    "technical.infrastructure", "admin.users", "admin.invites", "admin.roles",
})

FAMILY_CAPABILITIES = frozenset({
    capability for capability in CAPABILITIES
    if not capability.startswith(("technical.", "admin."))
})
ROLE_CAPABILITIES = {OWNER: CAPABILITIES, FAMILY_USER: FAMILY_CAPABILITIES}

TECHNICAL_TOOL_PREFIXES = (
    "get_server_", "get_service_", "get_project_", "list_ssh_",
    "list_server_", "remote_",
)
TECHNICAL_TOOL_NAMES = frozenset({"system_info", "get_top_processes"})


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    role: str
    status: str
    display_name: str = ""
    username: str = ""


class CapabilityPolicy:
    """Default-deny role policy shared by handlers and agent tools."""

    def allows(self, principal: Principal | None, capability: str) -> bool:
        return bool(
            principal and principal.status == ACTIVE
            and principal.role in VALID_ROLES
            and capability in ROLE_CAPABILITIES[principal.role]
        )

    def require(self, principal: Principal | None, capability: str) -> bool:
        allowed = self.allows(principal, capability)
        if not allowed and principal is not None:
            audit_logger.info(
                "forbidden_capability_attempt user_id=%s role=%s capability=%s",
                principal.user_id, principal.role, capability,
            )
        return allowed

    @staticmethod
    def tool_capability(tool_name: str) -> str:
        if tool_name in TECHNICAL_TOOL_NAMES or tool_name.startswith(
            TECHNICAL_TOOL_PREFIXES
        ):
            return "technical.infrastructure"
        if "reminder" in tool_name:
            return "reminders.personal.write"
        if tool_name == "get_user_location":
            return "location.personal.read"
        if "memory" in tool_name or tool_name in {"remember", "forget"}:
            return "memory.personal.read"
        return "tools.general"


class RateLimiter:
    """Small in-process abuse guard; persistence is unnecessary for short windows."""
    def __init__(self) -> None:
        self._messages: dict[int, deque[float]] = defaultdict(deque)
        self._searches: dict[int, deque[float]] = defaultdict(deque)

    @staticmethod
    def _allow(bucket: deque[float], window: int, limit: int) -> bool:
        now = time.monotonic()
        while bucket and bucket[0] <= now - window:
            bucket.popleft()
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True

    def message(self, principal: Principal) -> bool:
        return self._allow(self._messages[principal.user_id], 60,
                           120 if principal.role == OWNER else 30)

    def web_search(self, principal: Principal) -> bool:
        return self._allow(self._searches[principal.user_id], 3600,
                           240 if principal.role == OWNER else 60)


class AccessStorage:
    SCHEMA_VERSION = 1

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        return db

    def initialize(self, owner_ids: frozenset[int] = frozenset()) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS access_meta(
                    key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users(
                    telegram_user_id INTEGER PRIMARY KEY,
                    role TEXT NOT NULL CHECK(role IN ('owner','family_user')),
                    status TEXT NOT NULL CHECK(status IN ('active','disabled','removed')),
                    display_name TEXT NOT NULL DEFAULT '',
                    username TEXT NOT NULL DEFAULT '',
                    joined_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS family_invites(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_by_user_id INTEGER NOT NULL,
                    role TEXT NOT NULL CHECK(role='family_user'),
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    used_at TEXT, used_by_user_id INTEGER, revoked_at TEXT);
                CREATE INDEX IF NOT EXISTS idx_invites_pending
                    ON family_invites(expires_at, used_at, revoked_at);
                CREATE INDEX IF NOT EXISTS idx_users_role_status
                    ON users(role, status);
            """)
            now = _utc_now()
            for user_id in owner_ids:
                db.execute("""INSERT INTO users(telegram_user_id,role,status,joined_at,updated_at)
                    VALUES(?, 'owner', 'active', ?, ?)
                    ON CONFLICT(telegram_user_id) DO NOTHING""", (user_id, now, now))
            db.execute("""INSERT INTO access_meta(key,value) VALUES('schema_version',?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (str(self.SCHEMA_VERSION),))

    def validate_schema(self) -> bool:
        try:
            with self._connect() as db:
                version = db.execute(
                    "SELECT value FROM access_meta WHERE key='schema_version'"
                ).fetchone()
                tables = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )}
                unknown = db.execute(
                    "SELECT count(*) FROM users WHERE role NOT IN ('owner','family_user')"
                ).fetchone()[0]
            return bool(version and int(version[0]) == self.SCHEMA_VERSION
                        and {"users", "family_invites"} <= tables and not unknown)
        except (sqlite3.Error, ValueError):
            return False

    def principal(self, user_id: int) -> Principal | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE telegram_user_id=?", (user_id,)).fetchone()
        return Principal(user_id, row["role"], row["status"], row["display_name"], row["username"]) if row else None

    def create_invite(self, created_by: int, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self._connect() as db:
            db.execute("""INSERT INTO family_invites(token_hash,created_by_user_id,role,
                created_at,expires_at) VALUES(?,?,'family_user',?,?)""",
                (_hash(token), created_by, now.isoformat(),
                 (now + timedelta(seconds=ttl_seconds)).isoformat()))
        audit_logger.info("invite_created created_by_user_id=%s role=family_user", created_by)
        return token

    def redeem(self, token: str, user_id: int, display_name: str = "", username: str = "") -> str:
        digest = _hash(token)
        now = _utc_now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM family_invites WHERE token_hash=?", (digest,)).fetchone()
            if not row or row["revoked_at"] or row["used_at"] or row["expires_at"] <= now:
                return "invalid"
            existing = db.execute("SELECT role,status FROM users WHERE telegram_user_id=?", (user_id,)).fetchone()
            if existing:
                return "existing"
            db.execute("""INSERT INTO users(telegram_user_id,role,status,display_name,username,
                joined_at,updated_at) VALUES(?,'family_user','active',?,?,?,?)""",
                (user_id, display_name[:200], username[:100], now, now))
            updated = db.execute("""UPDATE family_invites SET used_at=?,used_by_user_id=?
                WHERE id=? AND used_at IS NULL AND revoked_at IS NULL""",
                (now, user_id, row["id"]))
            if updated.rowcount != 1:
                raise RuntimeError("Invite redemption race")
        audit_logger.info("invite_used role=family_user")
        audit_logger.info("family_user_added")
        return "created"

    def list_family(self) -> list[Principal]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM users WHERE role='family_user' ORDER BY joined_at").fetchall()
        return [Principal(r["telegram_user_id"], r["role"], r["status"], r["display_name"], r["username"]) for r in rows]

    def set_family_status(self, user_id: int, status: str) -> bool:
        if status not in {"active", "disabled", "removed"}:
            raise ValueError("Invalid status")
        with self._connect() as db:
            changed = db.execute("UPDATE users SET status=?,updated_at=? WHERE telegram_user_id=? AND role='family_user'",
                                 (status, _utc_now(), user_id)).rowcount == 1
        if changed:
            audit_logger.info("family_user_%s", {"active":"enabled","disabled":"disabled","removed":"removed"}[status])
        return changed

    def revoke_pending_invites(self, created_by: int) -> int:
        with self._connect() as db:
            count = db.execute("""UPDATE family_invites SET revoked_at=?
                WHERE created_by_user_id=? AND used_at IS NULL AND revoked_at IS NULL""",
                (_utc_now(), created_by)).rowcount
        if count:
            audit_logger.info("invite_revoked created_by_user_id=%s count=%s", created_by, count)
        return count

    def summary(self) -> dict[str, object]:
        now = _utc_now()
        with self._connect() as db:
            active = db.execute("SELECT count(*) FROM users WHERE role='family_user' AND status='active'").fetchone()[0]
            disabled = db.execute("SELECT count(*) FROM users WHERE role='family_user' AND status='disabled'").fetchone()[0]
            pending = db.execute("""SELECT count(*) FROM family_invites WHERE used_at IS NULL
                AND revoked_at IS NULL AND expires_at>?""", (now,)).fetchone()[0]
        return {"status": "ok" if self.validate_schema() else "error",
                "active_family_users": active, "pending_invites": pending,
                "disabled_users": disabled}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
