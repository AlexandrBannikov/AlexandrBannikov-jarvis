"""Dedicated SQLite store containing no conversation or Memory data."""
from contextlib import contextmanager
import os, sqlite3
from pathlib import Path
from typing import Iterator
from app.location.models import LocationCandidate, UserLocation

class LocationStorage:
    def __init__(self, path: str | Path): self.path = Path(path)
    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        db=sqlite3.connect(self.path, timeout=10); os.chmod(self.path, 0o600)
        db.row_factory=sqlite3.Row; db.execute("PRAGMA journal_mode=WAL")
        try: self.initialize(db); yield db; db.commit()
        except Exception: db.rollback(); raise
        finally: db.close()
    @staticmethod
    def initialize(db):
        db.executescript("""CREATE TABLE IF NOT EXISTS user_locations(
        id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER NOT NULL,
        latitude REAL NOT NULL,longitude REAL NOT NULL,city TEXT,country TEXT,
        timezone TEXT NOT NULL,timezone_source TEXT NOT NULL DEFAULT 'location',
        source TEXT NOT NULL DEFAULT 'telegram',location_type TEXT NOT NULL DEFAULT 'current',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        active INTEGER NOT NULL DEFAULT 1,UNIQUE(user_id,location_type));
        CREATE INDEX IF NOT EXISTS user_locations_owner_active ON user_locations(user_id,active);""")
    @staticmethod
    def _model(row):
        data=dict(row); data["active"]=bool(data["active"]); return UserLocation(**data)
    def save(self,user_id:int,item:LocationCandidate)->UserLocation:
        if user_id<=0: raise ValueError("invalid user_id")
        with self.connection() as db:
            db.execute("""INSERT INTO user_locations(user_id,latitude,longitude,city,country,timezone,timezone_source,source,location_type,active)
            VALUES(?,?,?,?,?,?,?,?,?,1) ON CONFLICT(user_id,location_type) DO UPDATE SET latitude=excluded.latitude,
            longitude=excluded.longitude,city=excluded.city,country=excluded.country,timezone=excluded.timezone,
            timezone_source=excluded.timezone_source,source=excluded.source,active=1,updated_at=CURRENT_TIMESTAMP""",
            (user_id,item.latitude,item.longitude,item.city,item.country,item.timezone,item.timezone_source,item.source,item.location_type))
            row=db.execute("SELECT * FROM user_locations WHERE user_id=? AND location_type=?",(user_id,item.location_type)).fetchone()
        return self._model(row)
    def get(self,user_id:int,location_type:str="current"):
        with self.connection() as db: row=db.execute("SELECT * FROM user_locations WHERE user_id=? AND location_type=? AND active=1",(user_id,location_type)).fetchone()
        return self._model(row) if row else None
    def clear(self,user_id:int,location_type:str="current")->bool:
        with self.connection() as db: cursor=db.execute("DELETE FROM user_locations WHERE user_id=? AND location_type=?",(user_id,location_type))
        return cursor.rowcount>0
    def count_active_users(self)->int:
        with self.connection() as db: row=db.execute("SELECT COUNT(DISTINCT user_id) count FROM user_locations WHERE active=1").fetchone()
        return int(row["count"])
    def validate_schema(self)->bool:
        try:
            with self.connection() as db: return db.execute("SELECT 1 FROM user_locations LIMIT 1").fetchone() is None or True
        except Exception: return False
