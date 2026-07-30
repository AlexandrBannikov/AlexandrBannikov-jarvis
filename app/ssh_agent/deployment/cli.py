"""Offline CLI for validating and rendering reviewed deployment kits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .inventory import InventoryError, load_inventory
from .manifest import build_manifest, manifest_json
from .renderer import RenderError, render_kit, verify_rendered


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.ssh_agent.deployment.cli"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("validate-inventory", "plan"):
        command = commands.add_parser(name)
        command.add_argument("inventory", type=Path)
    render = commands.add_parser("render")
    render.add_argument("inventory", type=Path)
    render.add_argument("--output", type=Path, required=True)
    inspect = commands.add_parser("inspect-manifest")
    inspect.add_argument("manifest", type=Path)
    verify = commands.add_parser("verify-rendered")
    verify.add_argument("directory", type=Path)
    return parser


def _inspect(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RenderError from error
    required = {
        "version", "local_steps", "server_steps",
        "expected_environment", "artifact_names",
    }
    if not isinstance(value, dict) or set(value) != required or value["version"] != 1:
        raise RenderError
    if not all(isinstance(value[name], list) for name in (
        "local_steps", "server_steps", "artifact_names"
    )):
        raise RenderError
    return value


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"validate-inventory", "plan", "render"}:
            inventory = load_inventory(args.inventory)
        if args.command == "validate-inventory":
            print(
                f"Inventory корректен. Серверов: {len(inventory.servers)}; "
                f"активных: {sum(server.enabled for server in inventory.servers)}"
            )
        elif args.command == "plan":
            print(manifest_json(build_manifest(inventory)), end="")
        elif args.command == "render":
            paths = render_kit(inventory, args.output)
            print(f"Deployment kit создан для review. Файлов: {len(paths)}")
            print("Ничего не выполнено; network и production не затронуты.")
        elif args.command == "inspect-manifest":
            value = _inspect(args.manifest)
            print(
                f"Manifest корректен. Серверных планов: "
                f"{len(value['server_steps'])}"
            )
        else:
            verify_rendered(args.directory)
            print("Rendered artifacts корректны и runtime config валиден.")
        return 0
    except (InventoryError, RenderError) as error:
        print(f"Ошибка: {error.code}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
