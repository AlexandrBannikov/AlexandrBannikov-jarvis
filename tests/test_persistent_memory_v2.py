"""Owner-aware persistent memory acceptance tests."""
from datetime import datetime, timedelta, timezone
import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.memory.cli import main
from app.memory.context_builder import MemoryContextBuilder
from app.memory.extractor import MemoryExtractor
from app.memory.security import redact_secrets
from app.memory.service import MemoryService
from app.memory.storage import MemoryStorage
from app.memory.manager import MemoryManager
from app.handlers import memory_command, memory_forget_command


@pytest.fixture
def service(tmp_path):
    return MemoryService(MemoryStorage(tmp_path/"memory.db"),
                         max_context_items=20,max_context_chars=1200)


def save(service,owner,key,value,**extra):
    return service.remember(owner_id=owner,scope=extra.pop("scope","project"),
        namespace=extra.pop("namespace","crypto-bot"),key=key,value=value,
        summary=extra.pop("summary",f"{key}: {value}"),**extra)


def test_upsert_owner_and_confidence(service):
    first=save(service,1,"commit","aaaaaaa",importance=4,confidence=.5)
    second=save(service,1,"commit","bbbbbbb",importance=9,confidence=.99)
    assert first.id==second.id
    assert service.recall(1,"commit")[0].value_json=="bbbbbbb"
    assert service.recall(2,"commit")==[]


def test_forget_is_owned(service):
    record=save(service,1,"path","/opt/crypto-bot")
    assert not service.forget(2,record.id)
    assert service.forget(1,record.id)


def test_expired_excluded_and_importance_order(service):
    past=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()
    save(service,1,"old","old",expires_at=past,importance=10)
    save(service,1,"low","low",importance=1)
    save(service,1,"high","high",importance=10)
    records=service.recall(1)
    assert [r.key for r in records]==["high","low"]


def test_projects_events_and_dedup(service):
    project=service.update_project(1,"crypto-bot",name="Crypto Bot",path="/opt/crypto-bot")
    one=service.record_project_event(1,"crypto-bot","tests","1012 passed",
                                     {"passed":1012},deduplication_key="tests-1012")
    two=service.record_project_event(1,"crypto-bot","tests","1012 passed",
                                     {"passed":1012},deduplication_key="tests-1012")
    status=service.get_project_context(1,"crypto-bot")
    assert project.path=="/opt/crypto-bot" and one.id==two.id
    assert len(status["events"])==1


@pytest.mark.parametrize(("text","key"),[
    ("Crypto Bot находится в /opt/crypto-bot.","path"),
    ("Последний полный прогон: 1012 passed","latest_tests"),
    ("commit 35d31a1","latest_commit"),
    ("Следующий этап — walk-forward analysis","next_task"),
])
def test_deterministic_extraction(text,key):
    assert key in {item.key for item in MemoryExtractor().extract(
        text,project_hint="crypto-bot")}


@pytest.mark.parametrize("text",["Привет","Как дела?","Что такое SQLite?"])
def test_extractor_ignores_chatter(text):
    assert MemoryExtractor().extract(text)==[]


@pytest.mark.parametrize("text",[
    "Запомни API_KEY=super-secret-value",
    "Запомни password=hunter2",
    "Запомни Bearer abcdefghijk",
])
def test_extractor_rejects_secrets(text):
    assert MemoryExtractor().extract(text)==[]
    redacted,changed=redact_secrets(text)
    assert changed and "[REDACTED]" in redacted


def test_context_grouping_dedup_and_limit(service):
    save(service,1,"lang","Responses in Russian.",scope="user_preference",
         namespace="profile",summary="Responses in Russian.",importance=10)
    save(service,1,"lang2","Responses in Russian.",scope="user_preference",
         namespace="profile",summary="Responses in Russian.",importance=9)
    context=service.build_user_context(1)
    assert "Persistent context" in context and "User preferences:" in context
    assert context.count("- Responses in Russian.")==1
    assert len(context)<=1200


def test_persistence_restart_secret_and_isolation(tmp_path):
    path=tmp_path/"memory.db"
    first=MemoryService(MemoryStorage(path))
    first.extract_and_remember(11,"Сервер разработки называется server-7rengh")
    first.extract_and_remember(11,"Crypto Bot находится в /opt/crypto-bot",
                               project_hint="crypto-bot")
    first.extract_and_remember(11,"commit 35d31a1",project_hint="crypto-bot")
    first.extract_and_remember(11,"1012 passed",project_hint="crypto-bot")
    first.extract_and_remember(11,"Не делай commit и push без моего разрешения")
    assert first.extract_and_remember(11,"API_KEY=do-not-store")==[]
    second=MemoryService(MemoryStorage(path))
    context=second.build_user_context(11)
    assert all(value in context for value in
               ("server-7rengh","/opt/crypto-bot","35d31a1","1012 passed",
                "Do not commit or push"))
    assert second.build_user_context(12)==""
    assert "do-not-store" not in context


def test_bootstrap_idempotent_and_cli_read_only(tmp_path,capsys):
    db=tmp_path/"memory.db"
    seed=tmp_path/"seed.json"
    seed.write_text(json.dumps({"projects":[{"project_key":"jarvis","name":"Jarvis",
        "facts":{"milestone":"memory"}}]}),encoding="utf-8")
    assert main(["--db",str(db),"bootstrap",str(seed)])==0
    assert main(["--db",str(db),"bootstrap",str(seed)])==0
    with sqlite3.connect(db) as connection:
        assert connection.execute("select count(*) from projects").fetchone()[0]==1
        assert connection.execute("select count(*) from memories").fetchone()[0]==1
    before=db.stat().st_mtime_ns
    assert main(["--db",str(db),"status","--owner-id","0"])==0
    assert db.stat().st_mtime_ns==before
    assert "active_memories" in capsys.readouterr().out


def test_telegram_memory_summary_and_owned_forget(tmp_path):
    manager=MemoryManager(MemoryStorage(tmp_path/"memory.db"))
    record=save(manager.service,123,"path","/opt/crypto-bot")
    update=SimpleNamespace(effective_user=SimpleNamespace(id=123),
        effective_message=SimpleNamespace(reply_text=AsyncMock()))
    context=SimpleNamespace(args=[],application=SimpleNamespace(
        bot_data={"memory_manager":manager}))
    asyncio.run(memory_command(update,context))
    assert "Активных записей: 1" in update.effective_message.reply_text.await_args.args[0]
    context.args=[str(record.id)]
    asyncio.run(memory_forget_command(update,context))
    assert manager.service.recall(123)==[]
