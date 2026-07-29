#!/usr/bin/env python3
"""Run a registered Jarvis tool from the command line."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.tools import create_default_tool_manager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a registered Jarvis tool.")
    parser.add_argument(
        "tool",
        choices=(
            "system_info",
            "remote_system_info",
            "remote_service_status",
        ),
    )
    parser.add_argument("--host", dest="host_alias")
    parser.add_argument("--service", dest="service_name")
    parser.add_argument(
        "--hosts-config",
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if args.tool == "system_info":
        if args.host_alias or args.service_name:
            parser.error("system_info does not accept remote arguments")
        parameters = {}
    elif args.tool == "remote_system_info":
        if not args.host_alias or args.service_name:
            parser.error("remote_system_info requires --host only")
        parameters = {"host_alias": args.host_alias}
    else:
        if not args.host_alias or not args.service_name:
            parser.error(
                "remote_service_status requires --host and --service"
            )
        parameters = {
            "host_alias": args.host_alias,
            "service_name": args.service_name,
        }

    result = create_default_tool_manager(args.hosts_config).execute(
        args.tool, **parameters
    )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
