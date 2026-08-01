"""Unit tests for controlled production rollout operations."""

from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from scripts import production_rollout as rollout


def paths_for(tmp_path: Path) -> rollout.RolloutPaths:
    project = tmp_path / "project"
    (project / "config").mkdir(parents=True)
    (project / "config/jarvis.service").write_text(
        "\n".join(
            [
                "User=jarvis",
                "Group=jarvis",
                "NoNewPrivileges=true",
                "PrivateTmp=true",
                "ProtectSystem=strict",
                "ProtectHome=true",
                "ReadWritePaths=/opt/jarvis/logs /opt/jarvis/data",
                "StateDirectoryMode=0700",
                "EnvironmentFile=/etc/jarvis/jarvis.env",
            ]
        ),
        encoding="utf-8",
    )
    return rollout.RolloutPaths(
        project_root=project,
        etc_dir=tmp_path / "etc/jarvis",
        systemd_dir=tmp_path / "systemd",
    )


def test_systemd_hardening_requires_private_state_directory(
    tmp_path: Path,
) -> None:
    paths = paths_for(tmp_path)
    unit = paths.source_unit
    unit.write_text(
        unit.read_text(encoding="utf-8").replace(
            "StateDirectoryMode=0700\n", ""
        ),
        encoding="utf-8",
    )

    assert not rollout._unit_is_hardened(unit)


def write_valid_environment(paths: rollout.RolloutPaths) -> None:
    token_name = "TELEGRAM_" + "BOT_TOKEN"
    key_name = "OPENAI_" + "API_KEY"
    paths.etc_dir.mkdir(parents=True, exist_ok=True)
    paths.env_file.write_text(
        "\n".join(
            [
                f"{token_name}=telegram-test-value",
                "TELEGRAM_ALLOWED_USER_IDS=123",
                "ALLOW_PUBLIC_ACCESS=false",
                "LLM_PROVIDER=openai",
                f"{key_name}=openai-test-value",
                "OPENAI_MODEL=test-model",
                "MAX_TOOL_ROUNDS=4",
                "JARVIS_WEB_SEARCH_ENABLED=true",
                "JARVIS_WEB_SEARCH_CONTEXT_SIZE=medium",
                "JARVIS_SSH_MODE=mock",
                f"JARVIS_HOSTS_CONFIG={paths.hosts_file}",
                "LOG_LEVEL=INFO",
                "HEALTH_HOST=127.0.0.1",
                "HEALTH_PORT=18090",
                f"ACCESS_DB_PATH={paths.project_root / 'access.db'}",
                "FAMILY_INVITE_TTL_SECONDS=86400",
            ]
        ),
        encoding="utf-8",
    )
    paths.hosts_file.write_text("hosts: {}\n", encoding="utf-8")
    paths.known_hosts.write_text("", encoding="utf-8")
    paths.keys_dir.mkdir(exist_ok=True)
    paths.etc_dir.chmod(0o750)
    paths.keys_dir.chmod(0o750)
    paths.env_file.chmod(0o640)
    paths.hosts_file.chmod(0o640)
    paths.known_hosts.chmod(0o640)


def test_prepare_preserves_existing_files_and_sets_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    paths.etc_dir.mkdir(parents=True)
    paths.env_file.write_text("existing", encoding="utf-8")
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rollout, "_ensure_system_user", lambda runner: None)
    monkeypatch.setattr(
        rollout, "_set_owner", lambda path, user, group: None
    )

    assert rollout.prepare(paths) == 0

    assert paths.env_file.read_text(encoding="utf-8") == "existing"
    assert paths.hosts_file.exists()
    assert paths.known_hosts.exists()
    assert paths.etc_dir.stat().st_mode & 0o777 == 0o750
    assert paths.env_file.stat().st_mode & 0o777 == 0o640
    assert paths.keys_dir.stat().st_mode & 0o777 == 0o750


def test_validate_ready_in_mock_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    write_valid_environment(paths)
    monkeypatch.setattr(rollout, "_owner_ok", lambda path: True)
    monkeypatch.setattr(rollout, "_port_available", lambda host, port: True)

    report = rollout.validate(paths, emit=False, secret_scan=lambda: [])

    assert report.ready
    assert ("WARN", "JARVIS_SSH_MODE=mock") in report.checks
    assert (
        "PASS",
        "OpenAI web search explicitly enabled",
    ) in report.checks


def test_validate_not_ready_with_empty_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    write_valid_environment(paths)
    text = paths.env_file.read_text(encoding="utf-8")
    paths.env_file.write_text(
        text.replace("TELEGRAM_ALLOWED_USER_IDS=123",
                     "TELEGRAM_ALLOWED_USER_IDS="),
        encoding="utf-8",
    )
    monkeypatch.setattr(rollout, "_owner_ok", lambda path: True)
    monkeypatch.setattr(rollout, "_port_available", lambda host, port: True)

    report = rollout.validate(paths, emit=False, secret_scan=lambda: [])

    assert not report.ready
    assert ("FAIL", "TELEGRAM_ALLOWED_USER_IDS is empty") in report.checks


def test_validate_initializes_enabled_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    write_valid_environment(paths)
    with paths.env_file.open("a", encoding="utf-8") as destination:
        destination.write(
            "\nMEMORY_ENABLED=true\n"
            f"MEMORY_DB_PATH={paths.data_dir / 'memory.db'}\n"
        )
    paths.data_dir.mkdir()
    monkeypatch.setattr(rollout, "_owner_ok", lambda path: True)
    monkeypatch.setattr(rollout, "_port_available", lambda host, port: True)

    report = rollout.validate(paths, emit=False, secret_scan=lambda: [])

    assert report.ready
    assert (
        "PASS",
        "Project memory SQLite schema and migrations",
    ) in report.checks
    assert (paths.data_dir / "memory.db").exists()


def test_real_ssh_mode_without_key_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    write_valid_environment(paths)
    paths.env_file.write_text(
        paths.env_file.read_text(encoding="utf-8").replace(
            "JARVIS_SSH_MODE=mock", "JARVIS_SSH_MODE=real"
        ),
        encoding="utf-8",
    )
    paths.hosts_file.write_text(
        "\n".join(
            [
                "hosts:",
                "  example:",
                "    hostname: 192.0.2.1",
                "    username: jarvis-monitor",
                f"    identity_file: {paths.keys_dir / 'missing_key'}",
                f"    known_hosts_file: {paths.known_hosts}",
                "    allowed_services: []",
            ]
        ),
        encoding="utf-8",
    )
    paths.hosts_file.chmod(0o640)
    monkeypatch.setattr(rollout, "_owner_ok", lambda path: True)
    monkeypatch.setattr(rollout, "_port_available", lambda host, port: True)

    report = rollout.validate(paths, emit=False, secret_scan=lambda: [])

    assert not report.ready
    assert any("SSH key for example" in message for _, message in report.checks)


def test_install_refuses_invalid_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 0)
    report = rollout.ValidationReport()
    report.fail("invalid")
    runner = Mock()

    result = rollout.install(
        paths,
        runner=runner,
        validator=lambda *args, **kwargs: report,
    )

    assert result == 1
    runner.assert_not_called()
    assert not paths.installed_unit.exists()


def test_start_failure_redacts_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    paths = paths_for(tmp_path)
    write_valid_environment(paths)
    paths.systemd_dir.mkdir()
    paths.installed_unit.write_text("unit", encoding="utf-8")
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 0)
    monkeypatch.setattr(rollout.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(rollout, "probe_health", lambda *args: False)
    ready = rollout.ValidationReport()
    ready.pass_("ready")
    secret = "telegram-test-value"
    authorization = "Authorization" + ": " + "Bearer " + secret

    def runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        if "status" in command or command[0] == "journalctl":
            return subprocess.CompletedProcess(
                command, 1, stdout=authorization, stderr=""
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="")

    result = rollout.start(
        paths,
        runner=runner,
        validator=lambda *args, **kwargs: ready,
        wait_seconds=0,
    )

    output = capsys.readouterr().out
    assert result == 1
    assert secret not in output
    assert "[REDACTED]" in output


def test_rollback_restores_latest_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    paths.systemd_dir.mkdir()
    paths.installed_unit.write_text("current", encoding="utf-8")
    (paths.systemd_dir / "jarvis.service.backup-20260101T000000Z").write_text(
        "backup", encoding="utf-8"
    )
    monkeypatch.setattr(rollout.os, "geteuid", lambda: 0)
    commands: list[list[str]] = []

    def runner(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
        del kwargs
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    assert rollout.rollback(paths, runner=runner) == 0

    assert paths.installed_unit.read_text(encoding="utf-8") == "backup"
    assert ["systemctl", "stop", "jarvis.service"] in commands
    assert ["systemctl", "disable", "jarvis.service"] in commands
    assert ["systemctl", "daemon-reload"] in commands


def test_redact_hides_supported_secret_forms() -> None:
    token = "123456789:" + "A" * 35
    key = "sk-" + "B" * 24
    authorization = "Authorization" + ": " + "Bearer " + "hidden"
    text = f"{token}\n{key}\n{authorization}\nidentity_file=/key"

    result = rollout.redact(
        text,
        {"TELEGRAM_BOT_TOKEN": token, "OPENAI_API_KEY": key},
    )

    assert token not in result
    assert key not in result
    assert "Bearer hidden" not in result
    assert "/key" not in result


def test_validate_initializes_enabled_reminders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = paths_for(tmp_path)
    write_valid_environment(paths)
    reminder_path = tmp_path.parent / f"{tmp_path.name}-runtime" / "reminders.db"
    with paths.env_file.open("a", encoding="utf-8") as destination:
        destination.write(
            "\nREMINDERS_ENABLED=true\n"
            f"REMINDERS_DB_PATH={reminder_path}\n"
            "REMINDERS_DEFAULT_TIMEZONE=Asia/Yekaterinburg\n"
        )
    monkeypatch.setattr(rollout, "_owner_ok", lambda path: True)
    monkeypatch.setattr(rollout, "_port_available", lambda host, port: True)

    report = rollout.validate(paths, emit=False, secret_scan=lambda: [])

    assert report.ready
    assert any(
        level == "PASS" and "Reminder SQLite" in message
        for level, message in report.checks
    )
