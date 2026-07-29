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
    parser.add_argument("tool", help="registered tool name")
    args = parser.parse_args()

    result = create_default_tool_manager().execute(args.tool)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
