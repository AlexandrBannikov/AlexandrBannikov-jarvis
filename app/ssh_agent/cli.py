"""Read-only diagnostics for SSH agent configuration."""

import argparse
import json
import os
from pathlib import Path

from .config import load_config
from .errors import SSHAgentError
from .registry import ServerRegistry
from .bootstrap import build_ssh_dependencies
from .service import ssh_enabled_from_environment


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.ssh_agent.cli")
    parser.add_argument("--config", type=Path, help="Путь к конфигурации для диагностики")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config")
    subparsers.add_parser("readiness")
    subparsers.add_parser("validate-runtime")
    subparsers.add_parser("health")
    subparsers.add_parser("list-servers")
    projects = subparsers.add_parser("list-projects")
    projects.add_argument("server_alias")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command in {"readiness", "validate-runtime", "health"}:
        dependencies = build_ssh_dependencies(
            enabled=ssh_enabled_from_environment(os.environ),
            config_path=args.config or Path(
                os.environ.get(
                    "JARVIS_SERVERS_CONFIG", "/etc/jarvis/servers.json"
                )
            ),
        )
        readiness = dependencies.readiness
        if args.command == "health":
            payload = {
                "ssh_enabled": readiness.enabled,
                "ssh_ready": readiness.ready,
                "ssh_readiness_code": readiness.code.value,
                "ssh_configuration_ok": readiness.configuration_ok,
                "ssh_known_hosts_ok": readiness.known_hosts_ok,
                "ssh_key_permissions_ok": readiness.key_permissions_ok,
                "ssh_executable_ok": readiness.executable_ok,
                "ssh_registered_servers_count": readiness.registered_servers_count,
                "ssh_enabled_servers_count": readiness.enabled_servers_count,
            }
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"SSH Agent: {'готов' if readiness.ready else 'не готов'}")
            print(f"Причина: {readiness.code.value}")
            print(
                "Конфигурация: "
                + ("корректна" if readiness.configuration_ok else "не готова")
            )
            print(f"Серверов: {readiness.registered_servers_count}")
            print(f"Активных: {readiness.enabled_servers_count}")
            print(f"OpenSSH: {'найден' if readiness.executable_ok else 'не найден'}")
            print(f"Ключи: {'проверены' if readiness.key_permissions_ok else 'не готовы'}")
            print(f"known_hosts: {'готов' if readiness.known_hosts_ok else 'не готов'}")
        return 0 if readiness.ready or not readiness.enabled else 1
    try:
        config = load_config(args.config)
        registry = ServerRegistry(config)
        if args.command == "validate-config":
            servers = registry.list_servers(include_disabled=True)
            print("Конфигурация SSH Agent корректна.")
            print(f"Серверов: {len(servers)}")
            print(f"Активных: {sum(server.enabled for server in servers)}")
        elif args.command == "list-servers":
            for server in registry.list_servers(include_disabled=True):
                status = "включён" if server.enabled else "отключён"
                print(f"{server.alias}: {status}")
        else:
            server = registry.get_server(args.server_alias, require_enabled=False)
            print(f"Сервер: {server.alias}")
            print(f"Статус: {'включён' if server.enabled else 'отключён'}")
            print("Проекты:")
            for project in registry.list_projects(
                server.alias, include_disabled_server=True
            ):
                print(f"- {project.alias}")
                for service in project.services:
                    print(f"  - {service}")
        return 0
    except SSHAgentError as error:
        print(f"Ошибка [{error.code}]: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
