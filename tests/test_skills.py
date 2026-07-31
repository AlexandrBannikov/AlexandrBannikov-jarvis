"""Tests for the production Skills Registry metadata layer."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Config
from app.handlers import skills_command
from app.health import health_payload, set_skill_health_provider
from app.skills.builtin import build_skill_registry
from app.skills.models import HealthStatus, SkillMetadata
from app.skills.registry import SkillRegistry
from app.tools import create_default_tool_manager
from app.tools.registry import ToolRegistry


def config(**overrides) -> Config:
    values = dict(
        telegram_bot_token="token", llm_provider="openai",
        openai_api_key="key", openai_model="model", openai_base_url=None,
        allow_public_access=True,
    )
    values.update(overrides)
    return Config(**values)


def test_registry_binds_existing_tools_and_is_deterministic(tmp_path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"), include_legacy_remote=False)
    registry = SkillRegistry(manager.registry)
    registry.register(SkillMetadata("core", "Core", "1", "core", "core", True, True, "builtin", ("system_info",)))
    assert registry.list()[0].skill_id == "core"
    assert registry.tools_for_skill("core") == ("system_info",)
    assert registry.summary()["ok"] == 1


def test_registry_rejects_unknown_and_conflicting_tools(tmp_path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"), include_legacy_remote=False)
    registry = SkillRegistry(manager.registry)
    registry.register(SkillMetadata("core", "Core", "1", "core", "core", True, True, "builtin", ("system_info",)))
    with pytest.raises(ValueError, match="already owned"):
        registry.register(SkillMetadata("other", "Other", "1", "other", "core", True, False, "builtin", ("system_info",)))
    with pytest.raises(ValueError, match="Unknown tool"):
        registry.register(SkillMetadata("missing", "Missing", "1", "missing", "core", True, False, "builtin", ("unknown",)))


def test_required_and_optional_health_failures_are_distinguished() -> None:
    tools = ToolRegistry()
    registry = SkillRegistry(tools)
    registry.register(SkillMetadata("required", "Required", "1", "required", "core", True, True, "builtin"), lambda: (HealthStatus.ERROR, "broken"))
    registry.register(SkillMetadata("optional", "Optional", "1", "optional", "monitoring", True, False, "builtin"), lambda: (HealthStatus.ERROR, "broken"))
    reports = {item.skill_id: item for item in registry.health()}
    assert reports["required"].health_status == HealthStatus.ERROR
    assert reports["optional"].health_status == HealthStatus.ERROR
    assert len(registry.required_errors()) == 1


def test_missing_dependency_is_an_error() -> None:
    registry = SkillRegistry(ToolRegistry())
    registry.register(SkillMetadata("dependent", "Dependent", "1", "dependent", "core", True, False, "builtin", dependencies=("missing",)))
    report = registry.health()[0]
    assert report.health_status == HealthStatus.ERROR
    assert "missing dependency" in report.health_message


def test_builtin_registry_reports_disabled_and_warning(tmp_path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"), include_legacy_remote=False)
    registry = build_skill_registry(manager.registry, config(), memory_manager=None)
    reports = {item.skill_id: item for item in registry.health()}
    assert reports["core"].health_status == HealthStatus.OK
    assert reports["memory"].health_status == HealthStatus.DISABLED
    assert reports["ssh"].health_status == HealthStatus.DISABLED


def test_skills_command_is_secret_free(tmp_path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"), include_legacy_remote=False)
    registry = build_skill_registry(manager.registry, config(), memory_manager=None)
    update = SimpleNamespace(effective_message=SimpleNamespace(reply_text=AsyncMock()))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"skill_registry": registry}))
    asyncio.run(skills_command(update, context))
    text = update.effective_message.reply_text.await_args.args[0]
    assert "Навыки Jarvis" in text
    assert "token" not in text and "key" not in text


def test_health_payload_contains_only_skill_summary(tmp_path) -> None:
    manager = create_default_tool_manager(str(tmp_path / "missing.yaml"), include_legacy_remote=False)
    registry = build_skill_registry(manager.registry, config(), memory_manager=None)
    set_skill_health_provider(registry)
    payload = health_payload()
    assert payload["skills"]["total"] == 5
    assert "token" not in str(payload)
    set_skill_health_provider(None)
