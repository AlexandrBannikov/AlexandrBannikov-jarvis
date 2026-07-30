import json
from pathlib import Path
import subprocess
import sys
import os


def write_config(tmp_path: Path) -> Path:
    data = {
        "version": 1,
        "servers": {
            "alpha": {
                "host": "localhost",
                "port": 22,
                "user": "jarvis-ops",
                "identity_file": "/private/identity/path",
                "host_key_alias": "alpha",
                "enabled": True,
                "projects": {
                    "app": {
                        "path": "/opt/app",
                        "services": ["app.service"],
                    }
                },
            },
            "beta": {
                "host": "localhost",
                "port": 22,
                "user": "jarvis-ops",
                "identity_file": "/another/private/path",
                "host_key_alias": "beta",
                "enabled": False,
                "projects": {
                    "worker": {
                        "path": "/opt/worker",
                        "services": ["worker.timer"],
                    }
                },
            },
        },
    }
    path = tmp_path / "servers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    return path


def run_cli(path: Path, *args: str, enabled: bool = False) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment["JARVIS_SSH_ENABLED"] = "true" if enabled else "false"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "app.ssh_agent.cli",
            "--config",
            str(path),
            *args,
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def test_validate_success(tmp_path: Path) -> None:
    result = run_cli(write_config(tmp_path), "validate-config")
    assert result.returncode == 0
    assert "корректна" in result.stdout
    assert "Серверов: 2" in result.stdout
    assert "Активных: 1" in result.stdout


def test_validate_failure_is_concise(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    result = run_cli(path, "validate-config")
    assert result.returncode != 0
    assert "CONFIG_NOT_FOUND" in result.stdout
    assert str(path) not in result.stdout
    assert "Traceback" not in result.stderr


def test_list_servers_never_prints_identity_paths(tmp_path: Path) -> None:
    result = run_cli(write_config(tmp_path), "list-servers")
    assert result.returncode == 0
    assert "alpha: включён" in result.stdout
    assert "beta: отключён" in result.stdout
    assert "/private/identity/path" not in result.stdout


def test_list_projects_including_disabled_server(tmp_path: Path) -> None:
    path = write_config(tmp_path)
    enabled = run_cli(path, "list-projects", "alpha")
    disabled = run_cli(path, "list-projects", "beta")
    assert enabled.returncode == 0
    assert "app" in enabled.stdout
    assert "app.service" in enabled.stdout
    assert disabled.returncode == 0
    assert "Статус: отключён" in disabled.stdout
    assert "worker.timer" in disabled.stdout
    assert "/another/private/path" not in disabled.stdout


def test_readiness_disabled_is_distinct_and_successful(tmp_path: Path) -> None:
    result = run_cli(tmp_path / "missing.json", "readiness")
    assert result.returncode == 0
    assert "SSH Agent: не готов" in result.stdout
    assert "SSH_DISABLED" in result.stdout
    assert str(tmp_path) not in result.stdout


def test_readiness_degraded_has_nonzero_exit_and_no_path(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    result = run_cli(path, "validate-runtime", enabled=True)
    assert result.returncode != 0
    assert "SSH_CONFIG_MISSING" in result.stdout
    assert str(path) not in result.stdout
    assert "Traceback" not in result.stderr


def test_health_output_is_safe_json(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    result = run_cli(path, "health", enabled=True)
    payload = json.loads(result.stdout)
    assert result.returncode != 0
    assert payload["ssh_ready"] is False
    assert payload["ssh_readiness_code"] == "SSH_CONFIG_MISSING"
    assert str(path) not in result.stdout
