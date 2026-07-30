"""Read-only diagnostics for SSH agent configuration."""

import argparse
from pathlib import Path

from .config import load_config
from .errors import SSHAgentError
from .registry import ServerRegistry


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.ssh_agent.cli")
    parser.add_argument("--config", type=Path, help="Путь к конфигурации для диагностики")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate-config")
    subparsers.add_parser("list-servers")
    projects = subparsers.add_parser("list-projects")
    projects.add_argument("server_alias")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
