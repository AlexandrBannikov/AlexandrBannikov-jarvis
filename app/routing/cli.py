"""Offline diagnostics for universal routing."""

from __future__ import annotations

import argparse
import json

from .capabilities import CAPABILITIES
from .router import UniversalRequestRouter


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.routing.cli")
    parser.add_argument("--json", action="store_true", dest="as_json")
    sub = parser.add_subparsers(dest="command", required=True)
    classify = sub.add_parser("classify")
    classify.add_argument("text")
    classify.add_argument("--location", choices=("saved", "none"), default="saved")
    classify.add_argument("--json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    capabilities = sub.add_parser("capabilities")
    capabilities.add_argument("--json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    validate = sub.add_parser("validate")
    validate.add_argument("--json", action="store_true", dest="as_json", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "classify":
        payload = UniversalRequestRouter().classify(
            args.text, location_available=args.location == "saved"
        ).to_dict()
    elif args.command == "capabilities":
        payload = {name: {"description": item.description, "external": item.external,
                          "personal": item.personal} for name, item in CAPABILITIES.items()}
    else:
        probes = ("Как погода?", "Проверь crypto-bot", "Что такое SSH?")
        router = UniversalRequestRouter()
        decisions = [router.classify(text, location_available=True) for text in probes]
        payload = {"valid": all(item.can_answer for item in decisions),
                   "capabilities_count": len(CAPABILITIES)}
    if args.as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, dict):
        for key, value in payload.items(): print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
