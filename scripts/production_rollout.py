#!/usr/bin/env python3
"""Controlled, secret-safe production rollout for Jarvis."""

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import grp
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
from typing import Callable

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app import __version__  # noqa: E402
from app.config import Config, load_config  # noqa: E402
from app.health import probe_health  # noqa: E402
from app.ssh_agent.bootstrap import build_ssh_dependencies  # noqa: E402
from app.memory import MemoryStorage  # noqa: E402
from app.reminders import ReminderStorage  # noqa: E402
from app.skills.builtin import build_skill_registry  # noqa: E402
from app.conversation import ConversationStorage  # noqa: E402
from app.startup import startup_self_check  # noqa: E402
from app.tools import create_default_tool_manager  # noqa: E402
from app.access import AccessStorage, CapabilityPolicy, Principal  # noqa: E402
from scripts.check_secrets import scan_repository  # noqa: E402

ENV_TEMPLATE = """TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_USER_IDS=
ALLOW_PUBLIC_ACCESS=false

LLM_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=
OPENAI_BASE_URL=
MAX_TOOL_ROUNDS=4
JARVIS_WEB_SEARCH_ENABLED=false
JARVIS_WEB_SEARCH_CONTEXT_SIZE=medium
MEMORY_ENABLED=false
MEMORY_MAX_CONTEXT=4000
MEMORY_MAX_RESULTS=7
MEMORY_AUTOSAVE=true
MEMORY_SUMMARIZATION=true
MEMORY_DB_PATH=/opt/jarvis/data/memory.db
REMINDERS_ENABLED=false
REMINDERS_DB_PATH=/var/lib/jarvis/reminders.db
REMINDERS_DEFAULT_TIMEZONE=Asia/Yekaterinburg
REMINDERS_POLL_INTERVAL_SECONDS=10
REMINDERS_MIN_LEAD_SECONDS=20
REMINDERS_MAX_ACTIVE_PER_USER=100
REMINDERS_MAX_MESSAGE_LENGTH=1000
REMINDERS_MAX_TITLE_LENGTH=120
REMINDERS_MAX_DELIVERY_ATTEMPTS=5
REMINDERS_RETRY_BASE_SECONDS=30
REMINDERS_OVERDUE_GRACE_SECONDS=86400
REMINDERS_MIN_RECURRENCE_SECONDS=3600
REMINDERS_DELIVERY_ENABLED=true
REMINDERS_LEASE_SECONDS=120
REMINDERS_LIST_LIMIT=20

# Deprecated compatibility settings; production SSH uses the Agent settings below.
JARVIS_SSH_MODE=real
JARVIS_HOSTS_CONFIG=/etc/jarvis/hosts.yaml
JARVIS_SSH_ENABLED=false
JARVIS_SERVERS_CONFIG=/etc/jarvis/servers.json

LOG_LEVEL=INFO
HEALTH_HOST=127.0.0.1
HEALTH_PORT=8090
TELEGRAM_STARTUP_NOTIFICATION=false
ACCESS_DB_PATH=/var/lib/jarvis/access.db
FAMILY_INVITE_TTL_SECONDS=86400
"""
HOSTS_TEMPLATE = """hosts: {}

# Example only. Replace empty values directly on the server:
# hosts:
#   crypto:
#     hostname:
#     port: 22
#     username: jarvis-monitor
#     identity_file: /etc/jarvis/keys/crypto_ed25519
#     known_hosts_file: /etc/jarvis/known_hosts
#     connect_timeout_seconds: 10
#     command_timeout_seconds: 15
#     allowed_services:
#       - crypto-paper.timer
#       - crypto-paper.service
"""


@dataclass(frozen=True, slots=True)
class RolloutPaths:
    project_root: Path = PROJECT_ROOT
    etc_dir: Path = Path("/etc/jarvis")
    systemd_dir: Path = Path("/etc/systemd/system")

    @property
    def env_file(self) -> Path:
        return self.etc_dir / "jarvis.env"

    @property
    def hosts_file(self) -> Path:
        return self.etc_dir / "hosts.yaml"

    @property
    def known_hosts(self) -> Path:
        return self.etc_dir / "known_hosts"

    @property
    def keys_dir(self) -> Path:
        return self.etc_dir / "keys"

    @property
    def source_unit(self) -> Path:
        return self.project_root / "config/jarvis.service"

    @property
    def installed_unit(self) -> Path:
        return self.systemd_dir / "jarvis.service"

    @property
    def logs_dir(self) -> Path:
        return self.project_root / "logs"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"


@dataclass(slots=True)
class ValidationReport:
    checks: list[tuple[str, str]] = field(default_factory=list)

    def pass_(self, message: str) -> None:
        self.checks.append(("PASS", message))

    def fail(self, message: str) -> None:
        self.checks.append(("FAIL", message))

    def warn(self, message: str) -> None:
        self.checks.append(("WARN", message))

    @property
    def ready(self) -> bool:
        return not any(level == "FAIL" for level, _ in self.checks)

    def emit(self) -> None:
        for level, message in self.checks:
            print(f"[{level}] {message}")
        print(f"Overall: {'READY' if self.ready else 'NOT READY'}")


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(
    command: list[str], *, check: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        capture_output=True,
        text=True,
    )


def _require_root() -> None:
    if os.geteuid() != 0:
        raise PermissionError("This command must be run as root")


def _ensure_system_user(runner: Runner = _run) -> None:
    if runner(["getent", "group", "jarvis"]).returncode != 0:
        runner(["groupadd", "--system", "jarvis"], check=True)
    if runner(["getent", "passwd", "jarvis"]).returncode != 0:
        runner(
            [
                "useradd",
                "--system",
                "--gid",
                "jarvis",
                "--no-create-home",
                "--home-dir",
                "/nonexistent",
                "--shell",
                "/usr/sbin/nologin",
                "jarvis",
            ],
            check=True,
        )


def _set_owner(path: Path, user: str, group: str) -> None:
    shutil.chown(path, user=user, group=group)


def _create_once(path: Path, content: str, mode: int) -> None:
    if path.exists():
        return
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "w", encoding="utf-8") as destination:
        destination.write(content)


def prepare(
    paths: RolloutPaths = RolloutPaths(),
    *,
    runner: Runner = _run,
) -> int:
    _require_root()
    _ensure_system_user(runner)
    for directory in (
        paths.etc_dir,
        paths.keys_dir,
        paths.logs_dir,
        paths.data_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    _create_once(paths.env_file, ENV_TEMPLATE, 0o640)
    _create_once(paths.hosts_file, HOSTS_TEMPLATE, 0o640)
    _create_once(paths.known_hosts, "", 0o640)

    for path, mode, owner, group in (
        (paths.etc_dir, 0o750, "root", "jarvis"),
        (paths.keys_dir, 0o750, "root", "jarvis"),
        (paths.env_file, 0o640, "root", "jarvis"),
        (paths.hosts_file, 0o640, "root", "jarvis"),
        (paths.known_hosts, 0o640, "root", "jarvis"),
        (paths.logs_dir, 0o750, "jarvis", "jarvis"),
        (paths.data_dir, 0o750, "jarvis", "jarvis"),
    ):
        os.chmod(path, mode)
        _set_owner(path, owner, group)
    for key in paths.keys_dir.iterdir():
        if key.is_file():
            os.chmod(key, 0o640)
            _set_owner(key, "root", "jarvis")

    print("Preparation complete. Manual actions required:")
    print("- Edit /etc/jarvis/jarvis.env directly on this server.")
    print("- Configure Telegram token, allowlist, OpenAI key and model.")
    print("- Keep SSH mode mock until keys and known_hosts are verified.")
    print("- Add SSH private keys manually; no keys were generated.")
    return 0


def _values(path: Path) -> dict[str, str]:
    return {
        key: value or ""
        for key, value in dotenv_values(path).items()
        if key is not None
    }


def _mode_ok(path: Path, expected: int) -> bool:
    return path.exists() and stat.S_IMODE(path.stat().st_mode) == expected


def _owner_ok(path: Path) -> bool:
    try:
        return (
            path.stat().st_uid == pwd.getpwnam("root").pw_uid
            and path.stat().st_gid == grp.getgrnam("jarvis").gr_gid
        )
    except (KeyError, OSError):
        return False


def _check_file(
    report: ValidationReport, path: Path, mode: int, label: str
) -> None:
    if _mode_ok(path, mode) and _owner_ok(path):
        report.pass_(f"{label} permissions")
    else:
        report.fail(f"{label} permissions or ownership")


def _unit_is_hardened(path: Path) -> bool:
    if not path.is_file():
        return False
    content = path.read_text(encoding="utf-8")
    required = (
        "User=jarvis",
        "Group=jarvis",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "ReadWritePaths=/opt/jarvis/logs /opt/jarvis/data",
        "StateDirectoryMode=0700",
        "EnvironmentFile=/etc/jarvis/jarvis.env",
    )
    return all(item in content for item in required)


def _port_available(host: str, port: int) -> bool:
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return probe_health(host, port, timeout=0.5)
    return True


def validate(
    paths: RolloutPaths = RolloutPaths(),
    *,
    emit: bool = True,
    secret_scan: Callable[[], list[object]] = scan_repository,
) -> ValidationReport:
    report = ValidationReport()
    if not paths.env_file.is_file():
        report.fail("Production environment file exists")
        if emit:
            report.emit()
        return report
    values = _values(paths.env_file)
    for name, label in (
        ("TELEGRAM_BOT_TOKEN", "Telegram token configured"),
        ("OPENAI_API_KEY", "OpenAI key configured"),
        ("OPENAI_MODEL", "OpenAI model configured"),
    ):
        if values.get(name, "").strip():
            report.pass_(label)
        else:
            report.fail(label)
    if values.get("TELEGRAM_ALLOWED_USER_IDS", "").strip():
        report.pass_("Telegram allowlist configured")
    else:
        report.fail("TELEGRAM_ALLOWED_USER_IDS is empty")
    try:
        config = load_config(values)
        report.pass_("Application configuration")
    except RuntimeError:
        config = None
        report.fail("Application configuration")

    if values.get("LLM_PROVIDER", "").strip().lower() == "openai":
        report.pass_("LLM provider is openai")
    else:
        report.fail("LLM provider is unsupported")
    if config is not None and config.web_search_enabled:
        report.pass_("OpenAI web search explicitly enabled")
    else:
        report.warn("OpenAI web search disabled")
    if config is not None:
        try:
            access = AccessStorage(config.access_db_path)
            access.initialize(config.telegram_allowed_user_ids)
            policy = CapabilityPolicy()
            owner_ok = all(
                access.principal(user_id) is not None
                and access.principal(user_id).role == "owner"
                for user_id in config.telegram_allowed_user_ids
            )
            family = Principal(1, "family_user", "active")
            matrix_ok = (
                policy.allows(family, "assistant.web_search")
                and not policy.allows(family, "technical.ssh")
                and not policy.allows(family, "admin.roles")
                and not policy.allows(family, "unknown")
            )
            if access.validate_schema() and owner_ok and matrix_ok:
                report.pass_("Family role schema, owner and capability matrix")
                report.pass_("Invite schema and one-time token storage")
                report.pass_("Family web search allowed; technical access denied")
            else:
                report.fail("Family access policy validation")
        except Exception:
            report.fail("Family access database and policy")
    if config is not None and config.memory_enabled:
        try:
            memory_storage = MemoryStorage(config.memory_db_path)
            memory_storage.initialize()
            if memory_storage.validate_schema():
                report.pass_("Project memory SQLite schema and migrations")
            else:
                report.fail("Project memory SQLite schema and migrations")
        except Exception:
            report.fail("Project memory SQLite database writable")
    else:
        report.warn("Project memory disabled")
    if config is not None and config.conversation_state_enabled and values.get("CONVERSATION_DB_PATH", "").strip():
        try:
            conversation_storage = ConversationStorage(
                config.conversation_db_path,
                ttl_minutes=config.conversation_state_ttl_minutes,
                max_messages=config.conversation_history_max_messages,
            )
            if conversation_storage.validate_schema():
                report.pass_("Conversation state SQLite schema and migrations")
            else:
                report.fail("Conversation state SQLite schema and migrations")
        except Exception:
            report.fail("Conversation state database and limits")
    else:
        report.warn("Conversation state disabled")
    if config is not None and config.reminders_enabled:
        try:
            database_path = config.reminders_db_path.resolve()
            project_path = paths.project_root.resolve()
            if database_path == project_path or project_path in database_path.parents:
                report.fail("Reminder database outside Git repository")
            else:
                report.pass_("Reminder database outside Git repository")
            database_path.parent.mkdir(parents=True, exist_ok=True)
            storage = ReminderStorage(database_path)
            storage.initialize()
            if storage.validate_schema():
                report.pass_("Reminder SQLite schema and migrations")
            else:
                report.fail("Reminder SQLite schema and migrations")
            if config.reminders_delivery_enabled and values.get(
                "TELEGRAM_BOT_TOKEN", ""
            ).strip():
                report.pass_("Reminder delivery Telegram configuration")
            elif config.reminders_delivery_enabled:
                report.fail("Reminder delivery Telegram configuration")
            else:
                report.warn("Reminder delivery disabled")
            report.pass_("Reminder scheduler and retry configuration")
        except Exception:
            report.fail("Reminder database directory and schema")
    else:
        report.warn("Reminders disabled")
    # hosts.yaml belongs to the deprecated pre-Agent SSH implementation. Keep it
    # on disk for compatibility, but do not make production readiness depend on it.
    if paths.hosts_file.is_file():
        report.warn("Legacy hosts.yaml retained (deprecated)")

    if config is not None:
        ssh = build_ssh_dependencies(
            enabled=config.ssh_enabled,
            config_path=config.ssh_servers_config_path,
        )
        if not config.ssh_enabled:
            report.warn("SSH Agent disabled")
        elif ssh.readiness.ready:
            report.pass_("SSH Agent readiness: SSH_READY")
            report.pass_(
                f"SSH Agent registered hosts: {ssh.readiness.registered_servers_count}"
            )
        else:
            report.fail(f"SSH Agent readiness: {ssh.readiness.code.value}")

    for path, mode, label in (
        (paths.etc_dir, 0o750, "/etc/jarvis"),
        (paths.env_file, 0o640, "jarvis.env"),
    ):
        _check_file(report, path, mode, label)
    if _unit_is_hardened(paths.source_unit):
        report.pass_("systemd unit hardening")
    else:
        report.fail("systemd unit hardening")

    try:
        manager = create_default_tool_manager(
            str(paths.hosts_file), include_legacy_remote=False
        )
        ssh_dependencies = None
        if config is not None:
            ssh_dependencies = build_ssh_dependencies(
                enabled=config.ssh_enabled,
                config_path=config.ssh_servers_config_path,
                tool_registry=manager.registry,
            )
        report.pass_(f"Tool registry: {len(manager.registry.list_tools())} tools")
        if config is not None:
            skills = build_skill_registry(
                manager.registry, config, ssh_dependencies=ssh_dependencies
            )
            required_errors = skills.required_errors()
            if required_errors:
                report.fail("Skills Registry required capability")
            else:
                report.pass_(f"Skills Registry: {len(skills.list())} skills")
    except Exception:
        report.fail("Tool registry or Skills Registry")
    if config is not None:
        try:
            startup_self_check(config)
            report.pass_("Startup self-check")
        except Exception:
            report.fail("Startup self-check")
        if _port_available(config.health_host, config.health_port):
            report.pass_("Health endpoint port available")
        else:
            report.fail("Health endpoint port unavailable")
    if secret_scan():
        report.fail("Git secret scan")
    else:
        report.pass_("Git secret scan")
    if emit:
        report.emit()
    return report


def install(
    paths: RolloutPaths = RolloutPaths(),
    *,
    runner: Runner = _run,
    validator: Callable[..., ValidationReport] = validate,
) -> int:
    _require_root()
    report = validator(paths, emit=True)
    if not report.ready:
        print("Installation refused: configuration is not ready.")
        return 1
    paths.systemd_dir.mkdir(parents=True, exist_ok=True)
    if paths.installed_unit.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = paths.systemd_dir / f"jarvis.service.backup-{timestamp}"
        shutil.copy2(paths.installed_unit, backup)
    shutil.copy2(paths.source_unit, paths.installed_unit)
    os.chmod(paths.installed_unit, 0o644)
    runner(["systemctl", "daemon-reload"], check=True)
    runner(["systemctl", "enable", "jarvis.service"], check=True)
    print("Service installed and enabled, but not started.")
    return 0


def redact(text: str, values: dict[str, str]) -> str:
    redacted = text
    for name in ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY"):
        value = values.get(name, "")
        if value:
            redacted = redacted.replace(value, "[REDACTED]")
    redacted = re.sub(
        r"(?i)(Authorization\s*:\s*Bearer)\s+\S+",
        r"\1 [REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"\b(?:sk-[A-Za-z0-9_-]{8,}|\d{8,12}:[A-Za-z0-9_-]{20,})\b",
        "[REDACTED]",
        redacted,
    )
    redacted = re.sub(
        r"(?im)^.*identity_file.*$",
        "identity_file=[REDACTED]",
        redacted,
    )
    return redacted


def start(
    paths: RolloutPaths = RolloutPaths(),
    *,
    runner: Runner = _run,
    validator: Callable[..., ValidationReport] = validate,
    wait_seconds: float = 3.0,
) -> int:
    _require_root()
    report = validator(paths, emit=True)
    if not report.ready or not paths.installed_unit.is_file():
        print("Start refused: validate/install must succeed first.")
        return 1
    runner(["systemctl", "start", "jarvis.service"], check=False)
    time.sleep(wait_seconds)
    active = runner(["systemctl", "is-active", "jarvis.service"])
    enabled = runner(["systemctl", "is-enabled", "jarvis.service"])
    values = _values(paths.env_file)
    config = load_config(values)
    healthy = probe_health(config.health_host, config.health_port)
    if active.returncode == 0 and enabled.returncode == 0 and healthy:
        print("Jarvis service is active, enabled and healthy.")
        return 0
    print("Jarvis failed to become healthy. Safe diagnostics:")
    for command in (
        ["systemctl", "status", "jarvis.service", "--no-pager"],
        ["journalctl", "-u", "jarvis.service", "-n", "50", "--no-pager"],
    ):
        result = runner(command)
        print(redact(result.stdout + result.stderr, values))
    return 1


def status(
    paths: RolloutPaths = RolloutPaths(), *, runner: Runner = _run
) -> int:
    values = _values(paths.env_file) if paths.env_file.is_file() else {}
    installed = paths.installed_unit.is_file()
    enabled_result = runner(["systemctl", "is-enabled", "jarvis.service"])
    active_result = runner(["systemctl", "is-active", "jarvis.service"])
    properties = runner(
        [
            "systemctl",
            "show",
            "jarvis.service",
            "--property=MainPID",
        ]
    ).stdout
    property_map = dict(
        line.split("=", 1)
        for line in properties.splitlines()
        if "=" in line
    )
    pid_text = property_map.get("MainPID", "0")
    uptime = "0s"
    if pid_text.isdigit() and int(pid_text) > 0:
        try:
            process_stat = Path(f"/proc/{pid_text}/stat").read_text(
                encoding="ascii"
            )
            start_ticks = int(process_stat.split()[21])
            clock_ticks = os.sysconf("SC_CLK_TCK")
            system_uptime = float(
                Path("/proc/uptime").read_text(encoding="ascii").split()[0]
            )
            uptime = f"{max(0, int(system_uptime - start_ticks / clock_ticks))}s"
        except (OSError, ValueError, IndexError):
            uptime = "unknown"
    hosts_count = 0
    try:
        hosts_count = len(load_hosts_config(paths.hosts_file).hosts)
    except Exception:
        pass
    tools_count = 0
    try:
        tools_count = len(
            create_default_tool_manager(
                str(paths.hosts_file)
            ).registry.list_tools()
        )
    except Exception:
        pass
    health = "no"
    if values:
        try:
            config = load_config(values)
            health = (
                "yes"
                if probe_health(config.health_host, config.health_port)
                else "no"
            )
        except RuntimeError:
            pass
    git_commit = _run(
        ["git", "-C", str(paths.project_root), "rev-parse", "--short", "HEAD"]
    ).stdout.strip()
    output = {
        "installed": "yes" if installed else "no",
        "enabled": "yes" if enabled_result.returncode == 0 else "no",
        "active": "yes" if active_result.returncode == 0 else "no",
        "PID": pid_text,
        "uptime": uptime,
        "health endpoint": health,
        "Telegram configured": "yes"
        if values.get("TELEGRAM_BOT_TOKEN", "").strip()
        else "no",
        "OpenAI configured": "yes"
        if values.get("OPENAI_API_KEY", "").strip()
        else "no",
        "SSH mode": values.get("JARVIS_SSH_MODE", "unknown"),
        "hosts": str(hosts_count),
        "tools": str(tools_count),
        "git commit": git_commit,
        "version": __version__,
    }
    for key, value in output.items():
        print(f"{key}: {value}")
    return 0


def rollback(
    paths: RolloutPaths = RolloutPaths(), *, runner: Runner = _run
) -> int:
    _require_root()
    runner(["systemctl", "stop", "jarvis.service"], check=False)
    runner(["systemctl", "disable", "jarvis.service"], check=False)
    backups = sorted(paths.systemd_dir.glob("jarvis.service.backup-*"))
    if backups:
        shutil.copy2(backups[-1], paths.installed_unit)
        print("Latest unit backup restored.")
    elif paths.installed_unit.exists():
        print("No unit backup found; installed unit was preserved.")
    runner(["systemctl", "daemon-reload"], check=True)
    print("Rollback complete. Configuration, keys, data and logs were preserved.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("prepare", "validate", "install", "start", "status", "rollback"),
    )
    args = parser.parse_args()
    commands = {
        "prepare": prepare,
        "validate": lambda: 0 if validate().ready else 1,
        "install": install,
        "start": start,
        "status": status,
        "rollback": rollback,
    }
    try:
        return commands[args.command]()
    except (PermissionError, subprocess.CalledProcessError, OSError) as error:
        print(f"Operation failed safely: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
