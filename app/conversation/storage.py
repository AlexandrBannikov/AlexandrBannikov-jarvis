from __future__ import annotations
import json, sqlite3
from datetime import timedelta
from pathlib import Path
from threading import RLock
from app.conversation.models import ConversationKey, ConversationState, PendingQuestion, utcnow
class ConversationStorage:
    def __init__(self, path: str | Path, *, ttl_minutes: int = 60, max_messages: int = 20) -> None:
        if not 5 <= ttl_minutes <= 1440: raise ValueError("ttl_minutes must be between 5 and 1440")
        if not 4 <= max_messages <= 100: raise ValueError("max_messages must be between 4 and 100")
        self.path=Path(path); self.ttl_minutes=ttl_minutes; self.max_messages=max_messages; self._lock=RLock(); self.initialize()
    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True); conn=sqlite3.connect(self.path,timeout=10); conn.row_factory=sqlite3.Row; conn.execute("PRAGMA journal_mode=WAL"); return conn
    def initialize(self) -> None:
        with self._lock, self._connect() as conn: conn.executescript("""CREATE TABLE IF NOT EXISTS conversation_states (owner_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,thread_id INTEGER NOT NULL DEFAULT 0,active_topic TEXT NOT NULL DEFAULT '',user_goal TEXT NOT NULL DEFAULT '',pending_question_json TEXT,collected_facts_json TEXT NOT NULL DEFAULT '{}',missing_facts_json TEXT NOT NULL DEFAULT '[]',last_assistant_action TEXT NOT NULL DEFAULT '',last_user_intent TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'active',confidence REAL NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,expires_at TEXT NOT NULL,PRIMARY KEY(owner_id,chat_id,thread_id)); CREATE TABLE IF NOT EXISTS conversation_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,owner_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,thread_id INTEGER NOT NULL DEFAULT 0,role TEXT NOT NULL,content TEXT NOT NULL,provenance TEXT NOT NULL DEFAULT 'RECENT_HISTORY',created_at TEXT NOT NULL,telegram_message_id INTEGER,reply_to_message_id INTEGER); CREATE INDEX IF NOT EXISTS idx_conversation_messages_scope ON conversation_messages(owner_id,chat_id,thread_id,id); CREATE INDEX IF NOT EXISTS idx_conversation_states_status ON conversation_states(status,expires_at);""")
    @staticmethod
    def _thread(value: int | None) -> int: return value or 0
    def get_state(self,key:ConversationKey)->ConversationState|None:
        with self._lock,self._connect() as conn: row=conn.execute("SELECT * FROM conversation_states WHERE owner_id=? AND chat_id=? AND thread_id=?",(key.owner_id,key.chat_id,self._thread(key.thread_id))).fetchone()
        if row is None:return None
        pending_raw = json.loads(row["pending_question_json"]) if row["pending_question_json"] else None
        state=ConversationState(key,row["active_topic"],row["user_goal"],PendingQuestion.from_dict(pending_raw) if isinstance(pending_raw, dict) else None,json.loads(row["collected_facts_json"]),json.loads(row["missing_facts_json"]),row["last_assistant_action"],row["last_user_intent"],row["status"],float(row["confidence"]),row["created_at"],row["updated_at"],row["expires_at"])
        if state.is_expired(): state.status="expired"
        return state
    def save_state(self,state:ConversationState)->None:
        now=utcnow(); state.updated_at=now.isoformat(); state.expires_at=(now+timedelta(minutes=self.ttl_minutes)).isoformat(); k=state.key
        with self._lock,self._connect() as conn: conn.execute("""INSERT INTO conversation_states VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(owner_id,chat_id,thread_id) DO UPDATE SET active_topic=excluded.active_topic,user_goal=excluded.user_goal,pending_question_json=excluded.pending_question_json,collected_facts_json=excluded.collected_facts_json,missing_facts_json=excluded.missing_facts_json,last_assistant_action=excluded.last_assistant_action,last_user_intent=excluded.last_user_intent,status=excluded.status,confidence=excluded.confidence,updated_at=excluded.updated_at,expires_at=excluded.expires_at""",(k.owner_id,k.chat_id,self._thread(k.thread_id),state.active_topic[:300],state.user_goal[:1000],json.dumps(state.pending_question.to_dict() if state.pending_question else None,ensure_ascii=False),json.dumps(dict(list(state.collected_facts.items())[:12]),ensure_ascii=False),json.dumps(state.missing_facts[:12]),state.last_assistant_action[:200],state.last_user_intent[:50],state.status[:30],max(0,min(1,state.confidence)),state.created_at,state.updated_at,state.expires_at))
    def append_message(self,key:ConversationKey,role:str,content:str,*,provenance:str="RECENT_HISTORY",telegram_message_id:int|None=None,reply_to_message_id:int|None=None)->None:
        if role not in {"user","assistant","tool"}: raise ValueError("invalid role")
        content=str(content).strip()[:4000]
        if not content:return
        with self._lock,self._connect() as conn:
            conn.execute("INSERT INTO conversation_messages(owner_id,chat_id,thread_id,role,content,provenance,created_at,telegram_message_id,reply_to_message_id) VALUES(?,?,?,?,?,?,?,?,?)",(key.owner_id,key.chat_id,self._thread(key.thread_id),role,content,provenance,utcnow().isoformat(),telegram_message_id,reply_to_message_id))
            conn.execute("DELETE FROM conversation_messages WHERE owner_id=? AND chat_id=? AND thread_id=? AND id NOT IN (SELECT id FROM conversation_messages WHERE owner_id=? AND chat_id=? AND thread_id=? ORDER BY id DESC LIMIT ?)",(key.owner_id,key.chat_id,self._thread(key.thread_id),key.owner_id,key.chat_id,self._thread(key.thread_id),self.max_messages))
    def recent_messages(self,key:ConversationKey)->list[dict[str,str]]:
        with self._lock,self._connect() as conn: rows=conn.execute("SELECT role,content,provenance FROM conversation_messages WHERE owner_id=? AND chat_id=? AND thread_id=? ORDER BY id DESC LIMIT ?",(key.owner_id,key.chat_id,self._thread(key.thread_id),self.max_messages)).fetchall()
        return [{"role":r["role"],"content":r["content"],"provenance":r["provenance"]} for r in reversed(rows)]
    def clear(self,key:ConversationKey)->None:
        with self._lock,self._connect() as conn: conn.execute("UPDATE conversation_states SET status='closed',pending_question_json=NULL,updated_at=? WHERE owner_id=? AND chat_id=? AND thread_id=?",(utcnow().isoformat(),key.owner_id,key.chat_id,self._thread(key.thread_id)))
    def active_sessions(self)->int:
        with self._lock,self._connect() as conn:return int(conn.execute("SELECT COUNT(*) FROM conversation_states WHERE status='active' AND expires_at>?",(utcnow().isoformat(),)).fetchone()[0])
    def pending_intents_count(self)->int:
        with self._lock,self._connect() as conn:return int(conn.execute("SELECT COUNT(*) FROM conversation_states WHERE status='active' AND expires_at>? AND pending_question_json IS NOT NULL AND pending_question_json!='null'",(utcnow().isoformat(),)).fetchone()[0])
    def validate_schema(self)->bool:
        with self._lock,self._connect() as conn:names={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {"conversation_states","conversation_messages"}<=names
