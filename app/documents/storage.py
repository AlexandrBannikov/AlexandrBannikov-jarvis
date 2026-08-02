"""Private SQLite metadata and chunk storage (separate from Memory)."""

from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3

from .models import DocumentChunk, DocumentSession, Provenance

SCHEMA="""
CREATE TABLE IF NOT EXISTS document_sessions(
 id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,chat_id INTEGER NOT NULL,
 telegram_message_id INTEGER NOT NULL,telegram_file_id_hash TEXT NOT NULL,
 original_filename TEXT NOT NULL,safe_filename TEXT NOT NULL,mime_type TEXT NOT NULL,
 file_size INTEGER NOT NULL,sha256 TEXT NOT NULL,status TEXT NOT NULL,
 document_type TEXT NOT NULL,extracted_char_count INTEGER NOT NULL DEFAULT 0,
 extracted_page_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,
 expires_at TEXT NOT NULL,last_accessed_at TEXT NOT NULL,error_code TEXT,is_active INTEGER NOT NULL DEFAULT 1,
 file_path TEXT);
CREATE INDEX IF NOT EXISTS ix_documents_owner ON document_sessions(user_id,chat_id,is_active,created_at);
CREATE INDEX IF NOT EXISTS ix_documents_expiry ON document_sessions(is_active,expires_at);
CREATE UNIQUE INDEX IF NOT EXISTS ix_documents_update ON document_sessions(user_id,chat_id,telegram_message_id);
CREATE TABLE IF NOT EXISTS document_chunks(
 document_id TEXT NOT NULL,chunk_index INTEGER NOT NULL,text TEXT NOT NULL,provenance TEXT NOT NULL,tokens TEXT NOT NULL,
 PRIMARY KEY(document_id,chunk_index),FOREIGN KEY(document_id) REFERENCES document_sessions(id) ON DELETE CASCADE);
"""

def _now(): return datetime.now(timezone.utc)

class DocumentSessionStorage:
    def __init__(self, db_path: Path, storage_path: Path): self.db_path=Path(db_path); self.storage_path=Path(storage_path)
    def initialize(self):
        self.storage_path.mkdir(parents=True,exist_ok=True,mode=0o700); os.chmod(self.storage_path,0o700)
        self.db_path.parent.mkdir(parents=True,exist_ok=True)
        with self._connect() as db: db.executescript(SCHEMA)
        os.chmod(self.db_path,0o600)
    def _connect(self):
        db=sqlite3.connect(self.db_path); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
    def validate_schema(self):
        try:
            with self._connect() as db: return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} >= {"document_sessions","document_chunks"}
        except sqlite3.Error: return False
    def insert(self,s:DocumentSession,chunks:list[DocumentChunk]):
        with self._connect() as db:
            db.execute("""INSERT INTO document_sessions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (s.id,s.user_id,s.chat_id,s.telegram_message_id,s.telegram_file_id_hash,s.original_filename,s.safe_filename,s.mime_type,s.file_size,s.sha256,s.status,s.document_type,s.extracted_char_count,s.extracted_page_count,s.created_at.isoformat(),s.expires_at.isoformat(),s.last_accessed_at.isoformat(),s.error_code,int(s.is_active),str(s.file_path) if s.file_path else None))
            db.executemany("INSERT INTO document_chunks VALUES(?,?,?,?,?)",[(s.id,c.index,c.text,json.dumps(c.provenance.__dict__ if hasattr(c.provenance,'__dict__') else {k:getattr(c.provenance,k) for k in ('page','sheet','row_start','row_end','section')}),json.dumps(sorted(c.normalized_tokens))) for c in chunks])
    def _session(self,row):
        if row is None:return None
        d=dict(row); return DocumentSession(**{**d,"created_at":datetime.fromisoformat(d["created_at"]),"expires_at":datetime.fromisoformat(d["expires_at"]),"last_accessed_at":datetime.fromisoformat(d["last_accessed_at"]),"is_active":bool(d["is_active"]),"file_path":Path(d["file_path"]) if d["file_path"] else None})
    def get(self,document_id,user_id,chat_id=None):
        sql="SELECT * FROM document_sessions WHERE id=? AND user_id=?"; args=[document_id,user_id]
        if chat_id is not None: sql+=" AND chat_id=?"; args.append(chat_id)
        with self._connect() as db: return self._session(db.execute(sql,args).fetchone())
    def by_message(self,user_id,chat_id,message_id):
        with self._connect() as db:return self._session(db.execute("SELECT * FROM document_sessions WHERE user_id=? AND chat_id=? AND telegram_message_id=?",(user_id,chat_id,message_id)).fetchone())
    def active(self,user_id,chat_id):
        with self._connect() as db:return self._session(db.execute("SELECT * FROM document_sessions WHERE user_id=? AND chat_id=? AND is_active=1 AND expires_at>? ORDER BY created_at DESC LIMIT 1",(user_id,chat_id,_now().isoformat())).fetchone())
    def list(self,user_id,chat_id=None,limit=20):
        sql="SELECT * FROM document_sessions WHERE user_id=? AND is_active=1 AND expires_at>?"; args=[user_id,_now().isoformat()]
        if chat_id is not None: sql+=" AND chat_id=?";args.append(chat_id)
        sql+=" ORDER BY created_at DESC LIMIT ?";args.append(limit)
        with self._connect() as db:return [self._session(r) for r in db.execute(sql,args)]
    def chunks(self,document_id,user_id,chat_id):
        if not self.get(document_id,user_id,chat_id): return []
        with self._connect() as db:
            rows=db.execute("SELECT * FROM document_chunks WHERE document_id=? ORDER BY chunk_index",(document_id,))
            return [DocumentChunk(r["chunk_index"],r["text"],Provenance(**json.loads(r["provenance"])),frozenset(json.loads(r["tokens"]))) for r in rows]
    def forget(self,document_id,user_id,chat_id=None):
        session=self.get(document_id,user_id,chat_id)
        if not session:return False
        if session.file_path:
            try: session.file_path.unlink(missing_ok=True)
            except OSError: pass
        with self._connect() as db:
            db.execute("DELETE FROM document_chunks WHERE document_id=?",(document_id,)); db.execute("UPDATE document_sessions SET is_active=0,status='forgotten',file_path=NULL WHERE id=? AND user_id=?",(document_id,user_id))
        return True
    def cleanup(self,apply=False):
        now=_now().isoformat()
        with self._connect() as db: rows=db.execute("SELECT id,user_id,file_path FROM document_sessions WHERE is_active=1 AND expires_at<=?",(now,)).fetchall()
        if apply:
            for row in rows:self.forget(row["id"],row["user_id"])
        return len(rows)
    def metrics(self):
        with self._connect() as db:
            active=db.execute("SELECT count(*) FROM document_sessions WHERE is_active=1 AND expires_at>?",(_now().isoformat(),)).fetchone()[0]
            expired=db.execute("SELECT count(*) FROM document_sessions WHERE is_active=1 AND expires_at<=?",(_now().isoformat(),)).fetchone()[0]
        return {"active":active,"expired":expired}
