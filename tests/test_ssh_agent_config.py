import json
import os
from pathlib import Path

import pytest

from app.ssh_agent.config import (
    MAX_PROJECTS,
    MAX_SERVERS,
    MAX_SERVICES,
    load_config,
)
from app.ssh_agent.errors import ConfigError, ErrorCode


def config_data(*, enabled: bool = True) -> dict:
    return {
        "version": 1,
        "servers": {
            "alpha": {
                "host": "127.0.0.1",
                "port": 22,
                "user": "jarvis-ops",
                "identity_file": "/etc/jarvis/ssh/id_ed25519",
                "host_key_alias": "alpha-local",
                "enabled": enabled,
                "projects": {
                    "app": {
                        "path": "/opt/app",
                        "services": ["app.service", "app.timer"],
                    }
                },
            }
        },
    }


def write_config(tmp_path: Path, data: object, mode: int = 0o600) -> Path:
    path = tmp_path / "servers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(mode)
    return path


def assert_error(path: Path, code: ErrorCode) -> None:
    with pytest.raises(ConfigError) as caught:
        load_config(path)
    assert caught.value.code == code
    assert str(path) not in str(caught.value)


def test_valid_one_server_and_immutable_typed_values(tmp_path: Path) -> None:
    config = load_config(write_config(tmp_path, config_data()))
    assert config.version == 1
    assert config.servers["alpha"].projects["app"].services == (
        "app.service",
        "app.timer",
    )
    with pytest.raises(TypeError):
        config.servers["other"] = config.servers["alpha"]  # type: ignore[index]


def test_multiple_servers_projects_and_enabled_states(tmp_path: Path) -> None:
    data = config_data()
    beta = json.loads(json.dumps(data["servers"]["alpha"]))
    beta["enabled"] = False
    beta["projects"]["worker"] = {
        "path": "/opt/worker",
        "services": ["worker.service"],
    }
    data["servers"]["beta"] = beta
    config = load_config(write_config(tmp_path, data))
    assert tuple(config.servers) == ("alpha", "beta")
    assert not config.servers["beta"].enabled
    assert tuple(config.servers["beta"].projects) == ("app", "worker")


def test_environment_override(tmp_path: Path) -> None:
    path = write_config(tmp_path, config_data())
    config = load_config(environment={"JARVIS_SERVERS_CONFIG": str(path)})
    assert "alpha" in config.servers


def test_example_config_validates_without_runtime_permission_check() -> None:
    example = Path(__file__).parents[1] / "config" / "servers.example.json"
    config = load_config(example, validate_permissions=False)
    assert tuple(config.servers) == ("jarvis", "crypto")


@pytest.mark.parametrize(
    ("mutation", "code"),
    [
        (lambda value: value.update(version=2), ErrorCode.CONFIG_INVALID_SCHEMA),
        (lambda value: value.update(extra=True), ErrorCode.CONFIG_INVALID_SCHEMA),
        (
            lambda value: value["servers"]["alpha"].update(extra=True),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"]["projects"]["app"].update(extra=True),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (lambda value: value.update(servers={}), ErrorCode.CONFIG_INVALID_SCHEMA),
        (
            lambda value: value["servers"].update({"Bad Alias": value["servers"].pop("alpha")}),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(port=0),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(host=""),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(host="h" * 254),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(user="bad user"),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(port=True),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(enabled="true"),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(identity_file="id_ed25519"),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"]["projects"]["app"].update(path="opt/app"),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"]["projects"]["app"].update(
                services=["not-a-unit"]
            ),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"]["projects"]["app"].update(
                services="app.service"
            ),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
        (
            lambda value: value["servers"]["alpha"].update(host="host\ncommand"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(host="host\u0000name"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(password="value"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(private_key="value"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(private_key_data="value"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(access_token="value"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(command="id"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(shell="/bin/sh"),
            ErrorCode.CONFIG_UNSAFE,
        ),
        (
            lambda value: value["servers"]["alpha"].update(host_key_alias="host;id"),
            ErrorCode.CONFIG_INVALID_SCHEMA,
        ),
    ],
)
def test_invalid_schema_values(tmp_path: Path, mutation, code: ErrorCode) -> None:
    data = config_data()
    mutation(data)
    assert_error(write_config(tmp_path, data), code)


def test_missing_file() -> None:
    assert_error(Path("/tmp/definitely-missing-jarvis-servers.json"), ErrorCode.CONFIG_NOT_FOUND)


def test_malformed_json(tmp_path: Path) -> None:
    path = tmp_path / "servers.json"
    path.write_text("{", encoding="utf-8")
    path.chmod(0o600)
    assert_error(path, ErrorCode.CONFIG_INVALID_JSON)


def test_root_must_be_object(tmp_path: Path) -> None:
    assert_error(write_config(tmp_path, []), ErrorCode.CONFIG_INVALID_SCHEMA)


def test_duplicate_alias_rejected(tmp_path: Path) -> None:
    server = json.dumps(config_data()["servers"]["alpha"])
    path = tmp_path / "servers.json"
    path.write_text(
        '{"version":1,"servers":{"alpha":'
        + server
        + ',"alpha":'
        + server
        + "}}",
        encoding="utf-8",
    )
    path.chmod(0o600)
    assert_error(path, ErrorCode.CONFIG_INVALID_SCHEMA)


def test_inline_private_key_rejected(tmp_path: Path) -> None:
    data = config_data()
    data["servers"]["alpha"]["host"] = "-----BEGIN " + "PRIVATE KEY-----"
    assert_error(write_config(tmp_path, data), ErrorCode.CONFIG_UNSAFE)


def test_limits(tmp_path: Path) -> None:
    data = config_data()
    template = data["servers"].pop("alpha")
    data["servers"] = {f"s{index}": template for index in range(MAX_SERVERS + 1)}
    assert_error(write_config(tmp_path, data), ErrorCode.CONFIG_INVALID_SCHEMA)

    data = config_data()
    data["servers"]["alpha"]["projects"] = {
        f"p{index}": {"path": f"/opt/p{index}", "services": []}
        for index in range(MAX_PROJECTS + 1)
    }
    assert_error(write_config(tmp_path, data), ErrorCode.CONFIG_INVALID_SCHEMA)

    data = config_data()
    data["servers"]["alpha"]["projects"]["app"]["services"] = [
        f"app{index}.service" for index in range(MAX_SERVICES + 1)
    ]
    assert_error(write_config(tmp_path, data), ErrorCode.CONFIG_INVALID_SCHEMA)


def test_unsafe_file_permissions(tmp_path: Path) -> None:
    assert_error(write_config(tmp_path, config_data(), 0o622), ErrorCode.CONFIG_UNSAFE)


def test_symlink_runtime_config(tmp_path: Path) -> None:
    target = write_config(tmp_path, config_data())
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable")
    assert_error(link, ErrorCode.CONFIG_UNSAFE)
