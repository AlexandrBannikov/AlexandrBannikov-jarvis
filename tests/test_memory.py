"""Tests for persistent local project memory."""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.ai.agent import JarvisAgent
from app.memory import MEMORY_TYPES, MemoryManager, MemoryStorage
from app.memory.ranking import rank_memory, terms
from app.memory.security import contains_secret
from app.memory.tools import register_memory_tools
from app.tools import create_default_tool_manager
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


@pytest.fixture
def memory(tmp_path) -> MemoryManager:
    return MemoryManager(
        MemoryStorage(tmp_path / "memory.db"),
        max_results=7,
        max_context=4_000,
        summary_threshold=100,
    )


def remember(
    manager: MemoryManager,
    content: str,
    *,
    title: str | None = None,
    memory_type: str = "fact",
    project: str = "jarvis",
    importance: int = 5,
    tags: tuple[str, ...] = (),
):
    return manager.remember(
        memory_type=memory_type,
        title=title or content,
        content=content,
        tags=tags,
        project=project,
        importance=importance,
        source="test",
    )


@pytest.mark.parametrize("memory_type", sorted(MEMORY_TYPES))
def test_remember_supports_every_memory_type(
    memory: MemoryManager, memory_type: str
) -> None:
    record = remember(
        memory,
        f"content for {memory_type}",
        memory_type=memory_type,
    )

    assert record.memory_type == memory_type
    assert record.is_active is True


def test_remember_persists_all_core_fields(memory: MemoryManager) -> None:
    record = memory.remember(
        memory_type="decision",
        title="Use SQLite",
        content="Project memory uses local SQLite.",
        tags=["memory", "sqlite"],
        project="jarvis",
        importance=9,
        source="user",
    )

    loaded = memory.storage.get(record.id)
    assert loaded == record
    assert loaded.tags == ("memory", "sqlite")
    assert loaded.importance == 9
    assert loaded.use_count == 0
    assert loaded.last_used_at is None


def test_remember_deduplicates_exact_content(memory: MemoryManager) -> None:
    first = remember(memory, "crypto-bot uses Bybit")
    second = remember(memory, "crypto-bot uses Bybit", title="duplicate")

    assert second.id == first.id
    assert len(memory.storage.list_active("jarvis")) == 1


def test_update_replaces_mutable_fields(memory: MemoryManager) -> None:
    record = remember(memory, "old information")

    updated = memory.update(
        record.id,
        title="New title",
        content="new information",
        tags=["new"],
        importance=8,
    )

    assert updated.title == "New title"
    assert updated.content == "new information"
    assert updated.tags == ("new",)
    assert updated.importance == 8


def test_update_unknown_memory_fails(memory: MemoryManager) -> None:
    with pytest.raises(KeyError, match="not found"):
        memory.update(
            999,
            title="title",
            content="content",
            tags=[],
            importance=5,
        )


def test_forget_soft_deletes_memory(memory: MemoryManager) -> None:
    record = remember(memory, "temporary test server")

    assert memory.forget(record.id) is True
    assert memory.storage.get(record.id).is_active is False
    assert memory.search("temporary server") == []


def test_forget_is_idempotently_false_after_first_call(
    memory: MemoryManager,
) -> None:
    record = remember(memory, "temporary")

    assert memory.forget(record.id) is True
    assert memory.forget(record.id) is False


def test_search_filters_by_project(memory: MemoryManager) -> None:
    remember(memory, "Bybit production", project="crypto")
    remember(memory, "Bybit documentation", project="jarvis")

    results = memory.search("Bybit", project="crypto")

    assert [record.project for record in results] == ["crypto"]
    assert results[0].content == "Bybit production"


def test_list_project_is_bounded(memory: MemoryManager) -> None:
    for index in range(5):
        remember(memory, f"record {index}")

    assert len(memory.list_project(max_results=3)) == 3


def test_search_marks_selected_records_used(memory: MemoryManager) -> None:
    record = remember(memory, "USA server is in Kansas City")

    memory.search("Kansas server")
    loaded = memory.storage.get(record.id)

    assert loaded.use_count == 1
    assert loaded.last_used_at is not None


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("crypto bot", {"crypto", "bot"}),
        ("USA-server status", {"usa-server", "status"}),
        ("Память проекта", {"память", "проекта"}),
        ("a x valid", {"valid"}),
    ],
)
def test_terms_normalization(query: str, expected: set[str]) -> None:
    assert terms(query) == expected


@pytest.mark.parametrize(
    ("title_match", "tag_match", "content_match"),
    [
        (True, False, False),
        (False, True, False),
        (False, False, True),
        (True, True, True),
    ],
)
def test_ranking_rewards_keyword_locations(
    memory: MemoryManager,
    title_match: bool,
    tag_match: bool,
    content_match: bool,
) -> None:
    record = remember(
        memory,
        "needle content" if content_match else "other content",
        title="needle" if title_match else "other",
        tags=("needle",) if tag_match else (),
    )

    assert rank_memory(record, "needle") > rank_memory(record, "absent")


def test_ranking_prefers_title_over_content(memory: MemoryManager) -> None:
    title = remember(memory, "other", title="crypto")
    content = remember(memory, "crypto", title="other")

    assert rank_memory(title, "crypto") > rank_memory(content, "crypto")


def test_ranking_prefers_importance_for_equal_matches(
    memory: MemoryManager,
) -> None:
    low = remember(memory, "server alpha low", importance=1)
    high = remember(memory, "server alpha high", importance=10)

    assert rank_memory(high, "server alpha") > rank_memory(low, "server alpha")


@pytest.mark.parametrize(
    ("text", "expected_type"),
    [
        ("Запомни что crypto-bot работает на Bybit.", "fact"),
        ("Запомни сервер USA находится в Kansas City.", "server"),
        ("Мы решили использовать SQLite.", "decision"),
        ("Теперь Jarvis использует Responses API.", "configuration"),
        ("запомните, что VPN сервер резервный", "server"),
        ("Запомни предпочтение отвечать кратко", "fact"),
    ],
)
def test_autosave_durable_statements(
    memory: MemoryManager, text: str, expected_type: str
) -> None:
    record = memory.autosave(text)

    assert record is not None
    assert record.memory_type == expected_type
    assert record.source == "autosave"


@pytest.mark.parametrize(
    "text",
    [
        "Привет",
        "Как дела?",
        "Расскажи шутку",
        "Что такое Linux?",
        "2 + 2",
        "Покажи этот лог",
        "Запомни",
    ],
)
def test_autosave_ignores_ordinary_conversation(
    memory: MemoryManager, text: str
) -> None:
    assert memory.autosave(text) is None
    assert memory.storage.list_active("jarvis") == []


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "TEST_SECRET_VALUE",
        "Bear" + "er private-token",
        "OPENAI_API_KEY" + "=private",
        "PASSWORD" + "=private",
        "-----BEGIN OPENSSH " + "PRIVATE KEY-----",
        "eyJ" + "abcdefghijk.abcdefghijk.signature",
        "config/.env",
        "123456789:" + "A" * 30,
        "192.168.1.10",
        "https://service.internal",
    ],
)
def test_secret_detection_blocks_memory(
    memory: MemoryManager, secret: str
) -> None:
    assert contains_secret(secret)
    with pytest.raises(ValueError, match="prohibited"):
        remember(memory, f"secret value {secret}")
    assert memory.autosave(f"Запомни {secret}") is None


def test_memory_content_is_not_logged(
    memory: MemoryManager, caplog: pytest.LogCaptureFixture
) -> None:
    registry = ToolRegistry()
    register_memory_tools(registry, memory)
    manager = ToolManager(registry)
    private_content = "private project fact"

    with caplog.at_level("INFO"):
        manager.execute(
            "remember",
            memory_type="fact",
            title="private title",
            content=private_content,
            tags="private",
            project="jarvis",
            importance=5,
            source="user",
        )

    assert private_content not in caplog.text
    assert "private title" not in caplog.text


def test_relevant_context_is_bounded(memory: MemoryManager) -> None:
    memory.max_context = 500
    for index in range(10):
        remember(memory, f"crypto information {index} " + "x" * 200)

    context = memory.relevant_context("crypto information")

    assert len(context) <= 500
    assert context.count("memory_id=") < 10


def test_relevant_context_excludes_unrelated_memory(
    memory: MemoryManager,
) -> None:
    remember(memory, "crypto-bot uses Bybit")
    remember(memory, "weather is sunny")

    context = memory.relevant_context("crypto-bot")

    assert "Bybit" in context
    assert "sunny" not in context


def test_summary_created_without_deleting_sources(tmp_path) -> None:
    manager = MemoryManager(
        MemoryStorage(tmp_path / "memory.db"),
        summary_threshold=3,
    )
    for index in range(3):
        remember(manager, f"project fact {index}")

    records = manager.storage.list_active("jarvis")
    summaries = [
        record
        for record in records
        if record.memory_type == "conversation_summary"
    ]
    assert len(summaries) == 1
    assert len([r for r in records if r.memory_type == "fact"]) == 3


def test_summary_not_duplicated_in_same_bucket(tmp_path) -> None:
    manager = MemoryManager(
        MemoryStorage(tmp_path / "memory.db"),
        summary_threshold=2,
    )
    remember(manager, "fact one")
    remember(manager, "fact two")
    manager.maybe_summarize()

    summaries = [
        record
        for record in manager.storage.list_active("jarvis")
        if record.memory_type == "conversation_summary"
    ]
    assert len(summaries) == 1


def test_summary_can_be_disabled(tmp_path) -> None:
    manager = MemoryManager(
        MemoryStorage(tmp_path / "memory.db"),
        summary_threshold=1,
        summarization=False,
    )

    remember(manager, "fact one")

    assert all(
        record.memory_type != "conversation_summary"
        for record in manager.storage.list_active("jarvis")
    )


def test_storage_initialization_is_idempotent(tmp_path) -> None:
    storage = MemoryStorage(tmp_path / "memory.db")

    storage.initialize()
    storage.initialize()

    assert storage.validate_schema()


def test_storage_migrates_legacy_table(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE memories "
            "(id INTEGER PRIMARY KEY, title TEXT, content TEXT)"
        )
        connection.execute(
            "INSERT INTO memories(title, content) VALUES ('old', 'legacy')"
        )
    storage = MemoryStorage(path)

    storage.initialize()

    assert storage.validate_schema()
    assert storage.get(1).content == "legacy"
    assert storage.get(1).project == "jarvis"


def test_storage_schema_has_required_columns(tmp_path) -> None:
    storage = MemoryStorage(tmp_path / "memory.db")
    storage.initialize()

    with sqlite3.connect(storage.path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(memories)")
        }

    assert {
        "id",
        "memory_type",
        "title",
        "content",
        "project",
        "importance",
        "use_count",
        "is_active",
    }.issubset(columns)


@pytest.mark.parametrize(
    "tool_name",
    [
        "remember",
        "forget",
        "update_memory",
        "search_memory",
        "list_project_memory",
    ],
)
def test_memory_tools_register_strict_schemas(
    memory: MemoryManager, tool_name: str
) -> None:
    registry = ToolRegistry()
    register_memory_tools(registry, memory)

    schema = registry.get(tool_name).parameters()

    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_memory_tool_crud_flow(memory: MemoryManager) -> None:
    registry = ToolRegistry()
    register_memory_tools(registry, memory)
    tools = ToolManager(registry)

    created = tools.execute(
        "remember",
        memory_type="fact",
        title="Crypto",
        content="crypto-bot uses Bybit",
        tags="crypto-bot,bybit",
        project="jarvis",
        importance=8,
        source="user",
    )
    memory_id = created.data["memory"]["id"]
    searched = tools.execute(
        "search_memory",
        query="crypto",
        project="jarvis",
        max_results=5,
    )
    updated = tools.execute(
        "update_memory",
        memory_id=memory_id,
        title="Crypto updated",
        content="crypto-bot uses Bybit only",
        tags="crypto-bot,bybit",
        importance=9,
    )
    forgotten = tools.execute("forget", memory_id=memory_id)

    assert created.success
    assert searched.data["memories"][0]["id"] == memory_id
    assert updated.data["memory"]["importance"] == 9
    assert forgotten.data["forgotten"] is True


class FakeProvider:
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


async def run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


def response(
    response_id: str,
    *,
    text: str = "",
    calls: list[object] | None = None,
) -> object:
    return SimpleNamespace(
        id=response_id,
        output_text=text,
        output=calls or [],
    )


def test_agent_injects_only_relevant_memory(memory: MemoryManager) -> None:
    remember(memory, "crypto-bot uses Bybit")
    remember(memory, "weather is sunny")
    provider = FakeProvider([response("r1", text="answer")])
    tool_manager = create_default_tool_manager("/missing")

    asyncio.run(
        JarvisAgent(
            provider,
            tool_manager,
            run_sync=run_immediately,
            memory_manager=memory,
        ).ask("What about crypto-bot?")
    )

    instructions = str(provider.requests[0]["instructions"])
    assert "Bybit" in instructions
    assert "sunny" not in instructions


def test_agent_autosaves_explicit_memory(memory: MemoryManager) -> None:
    provider = FakeProvider([response("r1", text="saved")])

    asyncio.run(
        JarvisAgent(
            provider,
            create_default_tool_manager("/missing"),
            run_sync=run_immediately,
            memory_manager=memory,
        ).ask("Запомни что сервер USA находится в Kansas City.")
    )

    assert memory.search("USA Kansas")


def test_agent_memory_and_web_search_coexist(memory: MemoryManager) -> None:
    remember(memory, "Python project uses stable releases")
    provider = FakeProvider([response("r1", text="answer")])

    asyncio.run(
        JarvisAgent(
            provider,
            create_default_tool_manager("/missing"),
            run_sync=run_immediately,
            memory_manager=memory,
            web_search_enabled=True,
        ).ask("Какая сейчас версия Python?")
    )

    assert "stable releases" in str(provider.requests[0]["instructions"])
    assert any(
        tool["type"] == "web_search"
        for tool in provider.requests[0]["tools"]
    )


def test_agent_memory_and_ssh_tools_coexist(memory: MemoryManager) -> None:
    remember(memory, "USA server alias is usa", memory_type="server")
    provider = FakeProvider([response("r1", text="answer")])

    asyncio.run(
        JarvisAgent(
            provider,
            create_default_tool_manager("/missing"),
            run_sync=run_immediately,
            memory_manager=memory,
        ).ask("Проверь сервер USA")
    )

    tool_names = {
        tool.get("name")
        for tool in provider.requests[0]["tools"]
        if tool["type"] == "function"
    }
    assert "remote_system_info" in tool_names
    assert "alias is usa" in str(provider.requests[0]["instructions"])


def test_agent_executes_memory_tool_call(memory: MemoryManager) -> None:
    tool_manager = create_default_tool_manager("/missing")
    register_memory_tools(tool_manager.registry, memory)
    provider = FakeProvider(
        [
            response(
                "r1",
                calls=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="call-memory",
                        name="remember",
                        arguments=(
                            '{"memory_type":"fact","title":"Bybit",'
                            '"content":"crypto-bot uses Bybit","tags":"crypto",'
                            '"project":"jarvis","importance":8,"source":"user"}'
                        ),
                    )
                ],
            ),
            response("r2", text="Запомнил."),
        ]
    )

    answer = asyncio.run(
        JarvisAgent(
            provider,
            tool_manager,
            run_sync=run_immediately,
            memory_manager=memory,
        ).ask("Сохрани durable fact")
    )

    assert answer == "Запомнил."
    assert memory.search("Bybit")
