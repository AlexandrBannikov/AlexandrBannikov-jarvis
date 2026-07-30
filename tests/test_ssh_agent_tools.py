import json
from datetime import datetime, timezone
from inspect import getsource
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.ai.agent import JarvisAgent
from app.ai.prompts import JARVIS_SYSTEM_PROMPT
from app.ai.tool_adapter import ToolAdapter, ToolCallValidationError
from app.ssh_agent.errors import ErrorCode
from app.ssh_agent.service_models import SSHRequestContext, SSHServiceResult
from app.ssh_agent.tools import (
    SSH_TOOL_NAMES, SSH_TOOL_TYPES, SSHServiceTool, register_ssh_tools,
)
from app.tools.manager import ToolManager
from app.tools.registry import ToolRegistry


EXPECTED = {
    "list_ssh_servers": (set(), set()),
    "list_server_projects": ({"server_alias"}, {"server_alias"}),
    "get_server_summary": ({"server_alias"}, {"server_alias"}),
    "get_server_disk_usage": ({"server_alias"}, {"server_alias"}),
    "get_server_memory_usage": ({"server_alias"}, {"server_alias"}),
    "get_server_uptime": ({"server_alias"}, {"server_alias"}),
    "get_service_status": (
        {"server_alias", "project_alias", "service_name"},
        {"server_alias", "project_alias", "service_name"},
    ),
    "get_service_recent_logs": (
        {"server_alias", "project_alias", "service_name", "lines"},
        {"server_alias", "project_alias", "service_name"},
    ),
    "get_project_status": (
        {"server_alias", "project_alias"}, {"server_alias", "project_alias"},
    ),
    "get_project_last_commit": (
        {"server_alias", "project_alias"}, {"server_alias", "project_alias"},
    ),
    "get_project_summary": (
        {"server_alias", "project_alias"}, {"server_alias", "project_alias"},
    ),
}
FORBIDDEN = {
    "user_id", "chat_id", "request_id", "source_message_id", "is_allowlisted",
    "host", "port", "username", "identity_file", "known_hosts",
    "host_key_alias", "project_path", "command", "argv", "timeout",
    "environment", "ssh_options",
}


class FakeService:
    def __init__(self) -> None:
        self.calls = []
        for tool_type in SSH_TOOL_TYPES:
            method = tool_type.service_method
            if not hasattr(self, method):
                setattr(self, method, AsyncMock(side_effect=self._result))

    async def _result(self, context, **arguments):
        self.calls.append((context, arguments))
        operation = "list_servers"
        return SSHServiceResult(
            True, operation, arguments.get("server_alias"),
            arguments.get("project_alias"), arguments.get("service_name"),
            data={"safe": "ok"},
        )

    @staticmethod
    def format(result):
        return "Безопасный результат."


def tools(service=None):
    registry = ToolRegistry()
    register_ssh_tools(registry, service or FakeService())
    return registry


def trusted() -> SSHRequestContext:
    return SSHRequestContext(
        123, 456, "telegram-123-456-7", datetime.now(timezone.utc),
        source_message_id=7, is_allowlisted=True,
    )


def test_exact_strict_schemas_and_no_identity_or_infrastructure_fields() -> None:
    registry = tools()
    assert SSH_TOOL_NAMES == set(EXPECTED)
    for tool in registry.list_tools():
        schema = tool.parameters()
        properties, required = EXPECTED[tool.name]
        assert set(schema["properties"]) == properties
        assert set(schema["required"]) == required
        assert schema["additionalProperties"] is False
        assert not (set(schema["properties"]) & FORBIDDEN)
    lines = registry.get("get_service_recent_logs").parameters()["properties"]["lines"]
    assert lines == {"type": "integer", "minimum": 1, "maximum": 200}


@pytest.mark.parametrize("field", ["user_id", "chat_id", "is_allowlisted", "command", "argv"])
def test_model_controlled_context_and_generic_arguments_rejected(field: str) -> None:
    adapter = ToolAdapter(tools())
    with pytest.raises(ToolCallValidationError):
        adapter.parse_and_validate("list_ssh_servers", json.dumps({field: 1}))
    with pytest.raises(ToolCallValidationError):
        adapter.parse_and_validate("get_service_recent_logs", json.dumps({
            "server_alias": "alpha", "project_alias": "app",
            "service_name": "app.service", "lines": 201,
        }))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "method"),
    [
        ("list_ssh_servers", {}, "list_servers"),
        ("list_server_projects", {"server_alias": "alpha"}, "list_projects"),
        ("get_server_summary", {"server_alias": "alpha"}, "get_server_summary"),
        ("get_server_disk_usage", {"server_alias": "alpha"}, "get_disk_usage"),
        ("get_server_memory_usage", {"server_alias": "alpha"}, "get_memory_usage"),
        ("get_server_uptime", {"server_alias": "alpha"}, "get_uptime"),
        ("get_service_status", {"server_alias": "alpha", "project_alias": "app",
                                "service_name": "app.service"}, "get_service_status"),
        ("get_service_recent_logs", {"server_alias": "alpha", "project_alias": "app",
                                     "service_name": "app.service", "lines": 30},
         "get_service_recent_logs"),
        ("get_project_status", {"server_alias": "alpha", "project_alias": "app"},
         "get_project_status"),
        ("get_project_last_commit", {"server_alias": "alpha", "project_alias": "app"},
         "get_project_last_commit"),
        ("get_project_summary", {"server_alias": "alpha", "project_alias": "app"},
         "get_project_summary"),
    ],
)
async def test_every_tool_calls_exactly_one_service_method(name, arguments, method) -> None:
    service = FakeService()
    tool = tools(service).get(name)
    result = await tool.execute_trusted(trusted(), arguments)
    assert result.success and result.data["formatted_message"] == "Безопасный результат."
    getattr(service, method).assert_awaited_once()
    assert sum(item.await_count for item in {
        getattr(service, kind.service_method) for kind in SSH_TOOL_TYPES
    }) == 1


@pytest.mark.asyncio
async def test_agent_dispatch_builds_context_once_and_rejects_missing_context() -> None:
    service = FakeService()
    registry = tools(service)
    agent = JarvisAgent(SimpleNamespace(), ToolManager(registry))
    call = {"type": "function_call", "call_id": "c1", "name": "get_server_uptime",
            "arguments": '{"server_alias":"alpha"}'}
    output = await agent._execute_call(
        call, user_id=123, chat_id=456, source_message_id=7,
        ssh_context=trusted(),
    )
    assert json.loads(output["output"])["success"] is True
    assert service.calls[0][0].user_id == 123
    service.calls.clear()
    denied = await agent._execute_call(
        call, user_id=123, chat_id=456, source_message_id=7, ssh_context=None,
    )
    payload = json.loads(denied["output"])
    assert payload["error"] == "SSH_CONTEXT_INVALID"
    assert not service.calls


@pytest.mark.asyncio
async def test_safe_output_redacts_and_omits_transport_internals() -> None:
    service = FakeService()
    service.get_uptime = AsyncMock(return_value=SSHServiceResult(
        True, "uptime", "alpha",
        data={"line": "Authorization: " + "Bearer abcdefghijklmnop"},
        truncated=True, partial=True,
    ))
    result = await tools(service).get("get_server_uptime").execute_trusted(
        trusted(), {"server_alias": "alpha"},
    )
    encoded = json.dumps(result.data, ensure_ascii=False)
    assert "[REDACTED]" in encoded
    for forbidden in ("secret.internal", "jarvis-ops", "/keys/", "known_hosts", "argv", "stderr"):
        assert forbidden not in encoded
    assert result.data["truncated"] is True and result.data["partial"] is True


def test_adapter_has_no_transport_plan_or_generic_dispatch() -> None:
    source = getsource(__import__("app.ssh_agent.tools", fromlist=["x"]))
    assert "transport" not in source
    assert "CommandPolicy" not in source and "build_plan" not in source
    assert not any(name in SSH_TOOL_NAMES for name in {
        "execute_command", "run_command", "shell", "terminal", "ssh",
        "restart", "deploy", "write_file",
    })


def test_agent_prompt_prioritizes_tools_and_rejects_write_or_shell_requests() -> None:
    prompt = JARVIS_SYSTEM_PROMPT.lower()
    assert "ssh tools" in prompt and "web_search" in prompt
    assert "не угадывай alias" in prompt
    assert "restart" in prompt and "произвольные команды отклоняй" in prompt
    assert "не утверждай" in prompt and "успешного результата tool" in prompt
