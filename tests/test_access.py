import asyncio
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.access import (
    AccessStorage, CapabilityPolicy, Principal, RateLimiter,
    CAPABILITIES, FAMILY_USER, OWNER,
)
from app.ai.agent import JarvisAgent
from app.handlers import authorize, start
from app.tools import create_default_tool_manager
from app.memory.storage import MemoryStorage
from app.memory.service import MemoryService
from app.bot import build_application
from app.config import Config


def storage(tmp_path: Path) -> AccessStorage:
    item = AccessStorage(tmp_path / "access.db")
    item.initialize(frozenset({1}))
    return item


def test_owner_bootstrap_and_schema_are_idempotent(tmp_path: Path) -> None:
    item = storage(tmp_path)
    item.initialize(frozenset({1}))
    assert item.validate_schema()
    assert item.principal(1) == Principal(1, OWNER, "active")


def test_capability_matrix_and_default_deny() -> None:
    policy = CapabilityPolicy()
    owner = Principal(1, OWNER, "active")
    family = Principal(2, FAMILY_USER, "active")
    assert all(policy.allows(owner, capability) for capability in CAPABILITIES)
    for capability in (
        "assistant.chat", "assistant.web_search", "assistant.weather",
        "memory.personal.read", "memory.family.write",
        "reminders.personal.write", "location.personal.write",
        "timezone.personal.read", "tools.general",
    ):
        assert policy.allows(family, capability)
    for capability in (
        "technical.ssh", "technical.systemd", "technical.logs",
        "technical.production_diagnostics", "admin.users", "admin.invites",
        "admin.roles", "unknown.capability",
    ):
        assert not policy.allows(family, capability)
    assert not policy.allows(None, "assistant.chat")
    assert not policy.allows(Principal(3, "unknown", "active"), "assistant.chat")
    assert not policy.allows(Principal(3, FAMILY_USER, "disabled"), "assistant.chat")


def test_invite_is_random_hashed_one_time_and_fixed_role(tmp_path: Path) -> None:
    item = storage(tmp_path)
    first = item.create_invite(1, 3600)
    second = item.create_invite(1, 3600)
    assert first != second and len(first) >= 40
    with sqlite3.connect(item.path) as db:
        stored = db.execute("SELECT token_hash,role FROM family_invites ORDER BY id LIMIT 1").fetchone()
    assert stored[0] != first and first not in stored[0]
    assert stored[1] == FAMILY_USER
    assert item.redeem(first, 2, "Wife", "wife") == "created"
    assert item.principal(2).role == FAMILY_USER
    assert item.redeem(first, 3) == "invalid"


def test_expired_and_revoked_invites_are_denied(tmp_path: Path) -> None:
    item = storage(tmp_path)
    expired = item.create_invite(1, 3600)
    with sqlite3.connect(item.path) as db:
        db.execute("UPDATE family_invites SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=1")
    assert item.redeem(expired, 2) == "invalid"
    revoked = item.create_invite(1, 3600)
    assert item.revoke_pending_invites(1) == 2
    assert item.redeem(revoked, 2) == "invalid"


def test_duplicate_user_does_not_consume_invite(tmp_path: Path) -> None:
    item = storage(tmp_path)
    token = item.create_invite(1, 3600)
    assert item.redeem(token, 1) == "existing"
    assert item.redeem(token, 2) == "created"


def test_disable_enable_remove_preserve_record(tmp_path: Path) -> None:
    item = storage(tmp_path)
    assert item.redeem(item.create_invite(1, 3600), 2) == "created"
    assert item.set_family_status(2, "disabled")
    assert item.principal(2).status == "disabled"
    assert item.set_family_status(2, "active")
    assert item.set_family_status(2, "removed")
    assert item.principal(2).status == "removed"


def test_health_summary_is_aggregate_only(tmp_path: Path) -> None:
    item = storage(tmp_path)
    token = item.create_invite(1, 3600)
    item.redeem(token, 2, "Private Name", "private")
    summary = item.summary()
    assert summary["active_family_users"] == 1
    assert "Private Name" not in str(summary) and "token" not in str(summary)


def test_unknown_start_without_invite_is_stopped(tmp_path: Path) -> None:
    from telegram.ext import ApplicationHandlerStop
    item = storage(tmp_path)
    update = SimpleNamespace(
        update_id=1, effective_user=SimpleNamespace(id=99),
        effective_message=SimpleNamespace(text="hello", reply_text=AsyncMock()),
    )
    context = SimpleNamespace(application=SimpleNamespace(bot_data={
        "access_storage": item,
        "config": SimpleNamespace(telegram_allowed_user_ids=frozenset({1})),
    }))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(authorize(update, context))
    assert "только по приглашению" in update.effective_message.reply_text.await_args.args[0]


def test_start_valid_invite_creates_family_user(tmp_path: Path) -> None:
    item = storage(tmp_path)
    token = item.create_invite(1, 3600)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=2, full_name="Wife", username="wife"),
        effective_message=SimpleNamespace(reply_text=AsyncMock()),
    )
    context = SimpleNamespace(args=[token], application=SimpleNamespace(
        bot_data={"access_storage": item}))
    asyncio.run(start(update, context))
    assert item.principal(2).role == FAMILY_USER
    assert "активирован" in update.effective_message.reply_text.await_args.args[0]


def test_rate_limits_are_role_aware() -> None:
    limiter = RateLimiter()
    family = Principal(2, FAMILY_USER, "active")
    assert all(limiter.message(family) for _ in range(30))
    assert not limiter.message(family)


async def immediate(function, *args, **kwargs):
    return function(*args, **kwargs)


def test_family_tool_schemas_hide_technical_tools(tmp_path: Path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"))
    agent = JarvisAgent(Mock(), manager, run_sync=immediate)
    schemas = agent._tool_schemas(
        allow_web=True, principal=Principal(2, FAMILY_USER, "active")
    )
    names = {schema.get("name") for schema in schemas}
    assert "system_info" not in names
    assert "remote_system_info" not in names


def test_family_technical_request_is_denied_before_provider(tmp_path: Path) -> None:
    provider = Mock()
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"))
    answer = asyncio.run(JarvisAgent(provider, manager, run_sync=immediate).ask(
        "Покажи состояние серверов", user_id=2,
        principal=Principal(2, FAMILY_USER, "active"),
    ))
    assert answer == "Техническое управление серверами доступно только владельцу."
    provider.create_response.assert_not_called()


class Provider:
    def __init__(self, result):
        self.result = result
        self.requests = []
    def create_response(self, **kwargs):
        self.requests.append(kwargs)
        return self.result


def test_family_user_can_use_hosted_web_search(tmp_path: Path) -> None:
    response = SimpleNamespace(
        id="r1", output_text="Актуальный ответ.", output=[
            {"type": "web_search_call", "status": "completed",
             "action": {"sources": [{"title": "Source", "url": "https://example.com"}]}},
            {"type": "message", "content": [{"annotations": []}]},
        ],
    )
    provider = Provider(response)
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"))
    answer = asyncio.run(JarvisAgent(
        provider, manager, run_sync=immediate, web_search_enabled=True,
    ).ask("Что произошло сегодня?", user_id=2,
          principal=Principal(2, FAMILY_USER, "active")))
    assert "Актуальный ответ" in answer
    assert any(tool["type"] == "web_search" for tool in provider.requests[0]["tools"])
    assert all(tool.get("name") != "system_info" for tool in provider.requests[0]["tools"])


def test_disabled_and_unknown_users_cannot_search(tmp_path: Path) -> None:
    provider = Mock()
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"))
    for principal in (Principal(2, "unknown", "active"),
                      Principal(2, FAMILY_USER, "disabled")):
        answer = asyncio.run(JarvisAgent(provider, manager, run_sync=immediate).ask(
            "Что произошло сегодня?", user_id=2, principal=principal,
        ))
        assert "только по приглашению" in answer
    provider.create_response.assert_not_called()


def test_guard_denies_technical_tool_before_execution(tmp_path: Path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"))
    manager.execute = Mock()
    agent = JarvisAgent(Mock(), manager, run_sync=immediate)
    output = asyncio.run(agent._execute_call(
        SimpleNamespace(call_id="c1", name="system_info", arguments="{}"),
        user_id=2, principal=Principal(2, FAMILY_USER, "active"),
    ))
    assert "CAPABILITY_DENIED" in output["output"]
    manager.execute.assert_not_called()


def test_family_memory_shared_and_technical_excluded(tmp_path: Path) -> None:
    memory = MemoryService(MemoryStorage(tmp_path / "memory.db"))
    memory.remember(owner_id=1, scope="environment", namespace="server",
                    key="secret-server", value="server-alpha",
                    summary="technical", source="test")
    memory.remember(owner_id=2, scope="user_preference", namespace="personal",
                    key="wife", value="private", summary="wife private", source="test")
    memory.remember_family("Семейная машина Skoda")
    context = memory.build_family_user_context(2)
    assert "Семейная машина Skoda" in context
    assert "wife private" in context
    assert "server-alpha" not in context and "secret-server" not in context
    assert "wife private" not in memory.build_family_user_context(3)


def test_production_application_wires_policy_into_agent(tmp_path: Path) -> None:
    config = Config(
        telegram_bot_token="123456:TEST_TOKEN_VALUE_abcdefghijklmnop",
        llm_provider="openai", openai_api_key="test-key",
        openai_model="test-model", openai_base_url=None,
        telegram_allowed_user_ids=frozenset({1}),
        access_db_path=tmp_path / "access.db",
        conversation_db_path=tmp_path / "conversation.db",
        location_db_path=tmp_path / "location.db",
        ssh_servers_config_path=tmp_path / "servers.json",
    )
    application = build_application(config)
    assert application.bot_data["agent"].capability_policy is application.bot_data["capability_policy"]
    assert application.bot_data["agent"].rate_limiter is application.bot_data["rate_limiter"]
