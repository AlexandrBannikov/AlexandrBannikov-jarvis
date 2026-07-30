import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.health import health_payload, set_ssh_health_provider
from app.ssh_agent.bootstrap import SSHDependencies, build_ssh_dependencies
from app.ssh_agent.config import load_config
from app.ssh_agent.errors import ErrorCode
from app.ssh_agent.metrics import SSHMetrics
from app.ssh_agent.readiness import SSHReadiness
from app.ssh_agent.service_models import SSHRequestContext
from app.ssh_agent.tools import SSHServiceTool, SSH_TOOL_NAMES
from app.tools.registry import ToolRegistry


def runtime(tmp_path: Path, *, key_mode: int = 0o600,
            hosts_mode: int = 0o600, config_mode: int = 0o600):
    tmp_path.mkdir(parents=True, exist_ok=True)
    key = tmp_path / "identity"
    key.write_text("TEST FIXTURE, NOT A PRIVATE KEY", encoding="utf-8")
    key.chmod(key_mode)
    known = tmp_path / "known_hosts"
    known.write_text("test fixture", encoding="utf-8")
    known.chmod(hosts_mode)
    executable = tmp_path / "ssh"
    executable.write_text("test executable placeholder", encoding="utf-8")
    executable.chmod(0o700)
    config = tmp_path / "servers.json"
    config.write_text(json.dumps({
        "version": 1,
        "servers": {
            "alpha": {
                "host": "SERVER_PLACEHOLDER", "port": 22,
                "user": "jarvis-ops", "identity_file": str(key),
                "host_key_alias": "alpha-example", "enabled": True,
                "projects": {"app": {
                    "path": "/opt/example",
                    "services": ["app.service"],
                }},
            }
        },
    }), encoding="utf-8")
    config.chmod(config_mode)
    return config, key, known, executable


def build(tmp_path: Path, **changes):
    config, key, known, executable = runtime(tmp_path)
    values = dict(enabled=True, config_path=config, known_hosts_path=known,
                  ssh_executable=executable)
    values.update(changes)
    return build_ssh_dependencies(**values), (config, key, known, executable)


def context() -> SSHRequestContext:
    return SSHRequestContext(
        1, 1, "readiness-test", datetime.now(timezone.utc),
        is_allowlisted=True,
    )


def test_disabled_needs_no_config_and_registers_safe_tools(tmp_path: Path) -> None:
    registry = ToolRegistry()
    dependencies = build_ssh_dependencies(
        enabled=False, config_path=tmp_path / "absent",
        tool_registry=registry,
    )
    assert dependencies.readiness.code is ErrorCode.SSH_DISABLED
    assert not dependencies.readiness.ready
    assert {tool.name for tool in registry.list_tools()} == SSH_TOOL_NAMES
    assert all(isinstance(tool, SSHServiceTool) for tool in registry.list_tools())


def test_valid_runtime_constructs_one_shared_ready_service(tmp_path: Path) -> None:
    registry = ToolRegistry()
    dependencies, _ = build(tmp_path, tool_registry=registry)
    assert dependencies.readiness.ready
    assert dependencies.readiness.code is ErrorCode.SSH_READY
    assert dependencies.readiness.registered_servers_count == 1
    assert all(tool.service is dependencies.service for tool in registry.list_tools())
    assert dependencies.service.metrics is dependencies.metrics


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("missing_key", ErrorCode.SSH_IDENTITY_FILE_MISSING),
        ("unsafe_key", ErrorCode.SSH_IDENTITY_FILE_UNSAFE),
        ("key_symlink", ErrorCode.SSH_IDENTITY_FILE_UNSAFE),
        ("missing_hosts", ErrorCode.SSH_KNOWN_HOSTS_MISSING),
        ("unsafe_hosts", ErrorCode.SSH_KNOWN_HOSTS_UNSAFE),
        ("hosts_symlink", ErrorCode.SSH_KNOWN_HOSTS_UNSAFE),
        ("missing_executable", ErrorCode.SSH_EXECUTABLE_MISSING),
    ],
)
def test_runtime_failures_are_stable(tmp_path: Path, mutation: str,
                                     expected: ErrorCode) -> None:
    dependencies, (config, key, known, executable) = build(tmp_path)
    assert dependencies.readiness.ready
    if mutation == "missing_key":
        key.unlink()
    elif mutation == "unsafe_key":
        key.chmod(0o644)
    elif mutation == "key_symlink":
        key.unlink()
        key.symlink_to(tmp_path / "target")
    elif mutation == "missing_hosts":
        known.unlink()
    elif mutation == "unsafe_hosts":
        known.chmod(0o666)
    elif mutation == "hosts_symlink":
        known.unlink()
        known.symlink_to(tmp_path / "target")
    else:
        executable.unlink()
    result = build_ssh_dependencies(
        enabled=True, config_path=config, known_hosts_path=known,
        ssh_executable=executable,
    )
    assert result.readiness.code is expected
    assert not result.readiness.ready


def test_config_missing_invalid_symlink_and_permissions(tmp_path: Path) -> None:
    missing = build_ssh_dependencies(
        enabled=True, config_path=tmp_path / "missing"
    )
    assert missing.readiness.code is ErrorCode.SSH_CONFIG_MISSING
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{", encoding="utf-8")
    invalid.chmod(0o600)
    assert build_ssh_dependencies(
        enabled=True, config_path=invalid
    ).readiness.code is ErrorCode.SSH_CONFIG_INVALID
    link = tmp_path / "link.json"
    link.symlink_to(invalid)
    assert build_ssh_dependencies(
        enabled=True, config_path=link
    ).readiness.code is ErrorCode.SSH_CONFIG_PERMISSIONS_UNSAFE
    config, *_ = runtime(tmp_path / "unsafe")
    config.chmod(0o666)
    assert build_ssh_dependencies(
        enabled=True, config_path=config
    ).readiness.code is ErrorCode.SSH_CONFIG_PERMISSIONS_UNSAFE


@pytest.mark.asyncio
async def test_degraded_service_fails_before_transport(tmp_path: Path) -> None:
    dependencies = build_ssh_dependencies(
        enabled=True, config_path=tmp_path / "missing"
    )
    result = await dependencies.service.get_uptime(context(), "unknown")
    assert result.error_code is ErrorCode.SSH_CONFIG_MISSING
    assert dependencies.metrics.total_requests == 1


def test_health_disabled_ready_degraded_and_metrics(tmp_path: Path) -> None:
    disabled = build_ssh_dependencies(
        enabled=False, config_path=tmp_path / "missing"
    )
    set_ssh_health_provider(disabled)
    payload = health_payload()
    assert payload["status"] == "ok"
    assert payload["ssh_enabled"] is False
    assert payload["ssh_readiness_code"] == "SSH_DISABLED"

    ready, paths = build(tmp_path / "ready")
    ready.metrics.active_requests = 2
    ready.metrics.total_requests = 7
    ready.metrics.total_failures = 1
    ready.metrics.last_success_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ready.metrics.last_error_code = ErrorCode.SSH_BUSY
    set_ssh_health_provider(ready)
    payload = health_payload()
    assert payload["ssh_ready"] is True
    assert payload["ssh_active_requests"] == 2
    assert payload["ssh_last_error_code"] == "SSH_BUSY"
    encoded = json.dumps(payload)
    for secret in (
        "SERVER_PLACEHOLDER", "jarvis-ops",
        str(paths[1]), str(paths[2]),
    ):
        assert secret not in encoded

    degraded = build_ssh_dependencies(
        enabled=True, config_path=tmp_path / "missing"
    )
    set_ssh_health_provider(degraded)
    assert health_payload()["ssh_readiness_code"] == "SSH_CONFIG_MISSING"


def test_validation_source_is_network_and_key_content_free() -> None:
    import app.ssh_agent.readiness as module
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source and "socket" not in source
    assert "read_text" not in source and "read_bytes" not in source


def test_example_config_is_safe_and_valid() -> None:
    path = Path("config/servers.example.json")
    config = load_config(path, validate_permissions=False)
    assert config.servers
    text = path.read_text(encoding="utf-8")
    assert "127.0.0.1" not in text
    assert "SERVER_PLACEHOLDER" in text
    assert all(not server.enabled for server in config.servers.values())


def test_docs_and_ignore_rules_cover_operator_safety() -> None:
    deployment = Path("docs/ssh-agent-deployment.md").read_text(encoding="utf-8")
    remote = Path("docs/ssh-agent-remote-account.md").read_text(encoding="utf-8")
    ignored = Path(".gitignore").read_text(encoding="utf-8")
    assert "StrictHostKeyChecking=no" in deployment
    assert "JARVIS_SSH_ENABLED=false" in deployment
    assert "Unrestricted root SSH" in remote
    assert "no-port-forwarding" in remote
    for pattern in ("*.pem", "*.key", "/known_hosts", "/config/servers.json"):
        assert pattern in ignored
