#!/usr/bin/env python3
"""Validate Jarvis production configuration without exposing secrets."""

import argparse
from pathlib import Path
import sys

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import load_config  # noqa: E402

DEFAULT_ENV_FILE = Path("/etc/jarvis/jarvis.env")


def _is_configured(values: dict[str, str], name: str) -> bool:
    return bool(values.get(name, "").strip())


def check_config(env_file: Path) -> int:
    """Validate an env file and print only presence statuses."""
    if not env_file.is_file():
        print("Production environment file: missing")
        print("Configuration invalid")
        return 1

    raw_values = dotenv_values(env_file)
    values = {
        key: value or ""
        for key, value in raw_values.items()
        if key is not None
    }

    fields = ("TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "OPENAI_MODEL")
    for field in fields:
        status = "configured" if _is_configured(values, field) else "missing"
        print(f"{field}: {status}")

    try:
        config = load_config(values)
        if config.llm_provider == "openai" and not config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required")
    except RuntimeError:
        print("Configuration invalid")
        return 1

    print("Configuration valid")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Safely validate Jarvis production configuration."
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=DEFAULT_ENV_FILE,
        help="environment file to validate",
    )
    args = parser.parse_args()
    return check_config(args.env_file)


if __name__ == "__main__":
    raise SystemExit(main())
