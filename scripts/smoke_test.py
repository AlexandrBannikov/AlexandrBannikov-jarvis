#!/usr/bin/env python3
"""Explicit offline or live smoke tests for Jarvis."""

import argparse
import asyncio
from pathlib import Path
import sys
import tempfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai.provider import LLMProvider  # noqa: E402
from app.config import load_config  # noqa: E402
from app.health import health_payload  # noqa: E402
from app.infrastructure.hosts import load_hosts_config  # noqa: E402
from app.infrastructure.ssh_client import SSHClient  # noqa: E402
from app.tools import create_default_tool_manager  # noqa: E402


class _FakeOpenAI(LLMProvider):
    def generate_response(
        self, prompt: str, system_prompt: str | None = None
    ) -> str:
        del prompt, system_prompt
        return "offline-ok"


def offline_smoke_test() -> bool:
    """Exercise local components with no external network or SSH."""
    with tempfile.TemporaryDirectory() as directory:
        hosts_file = Path(directory) / "hosts.yaml"
        hosts_file.write_text("hosts: {}\n", encoding="utf-8")
        values = {
            "TELEGRAM_BOT_TOKEN": "offline-placeholder",
            "TELEGRAM_ALLOWED_USER_IDS": "1",
            "ALLOW_PUBLIC_ACCESS": "false",
            "LLM_PROVIDER": "openai",
            "OPENAI_API_KEY": "offline-placeholder",
            "OPENAI_MODEL": "offline-model",
            "JARVIS_SSH_MODE": "mock",
            "JARVIS_HOSTS_CONFIG": str(hosts_file),
            "HEALTH_HOST": "127.0.0.1",
            "HEALTH_PORT": "8090",
        }
        config = load_config(values)
        manager = create_default_tool_manager(str(config.jarvis_hosts_config))
        tool_result = manager.execute("system_info")
        fake_result = _FakeOpenAI().generate_response("ping")
        health_ok = health_payload().get("status") == "ok"
    checks = (
        config.jarvis_ssh_mode == "mock",
        len(manager.registry.list_tools()) >= 1,
        tool_result.success,
        fake_result == "offline-ok",
        health_ok,
    )
    return all(checks)


async def _telegram_get_me(token: str) -> bool:
    from telegram import Bot
    from telegram.request import HTTPXRequest

    request = HTTPXRequest(
        connect_timeout=5,
        read_timeout=5,
        write_timeout=5,
        pool_timeout=5,
    )
    async with Bot(token=token, request=request) as bot:
        return bool((await bot.get_me()).id)


def live_smoke_test(host_alias: str | None = None) -> bool:
    """Run explicitly authorized minimal live checks with short timeouts."""
    from app.ai.openai_provider import OpenAIProvider
    from scripts.production_rollout import RolloutPaths, _values

    paths = RolloutPaths()
    values = _values(paths.env_file)
    config = load_config(values)
    telegram_ok = asyncio.run(_telegram_get_me(config.telegram_bot_token))
    provider = OpenAIProvider(
        api_key=config.openai_api_key,
        model=config.openai_model,
        base_url=config.openai_base_url,
        timeout=5,
    )
    openai_ok = bool(provider.generate_response("Reply with OK only."))
    ssh_ok = True
    if config.jarvis_ssh_mode == "real":
        if not host_alias:
            raise RuntimeError("--host-alias is required in real SSH mode")
        host = load_hosts_config(
            config.jarvis_hosts_config, required=True
        ).get(host_alias)
        ssh_ok = SSHClient(host).test_connection()
    return telegram_ok and openai_ok and ssh_ok


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--offline", action="store_true")
    mode.add_argument("--live", action="store_true")
    parser.add_argument("--host-alias")
    args = parser.parse_args(argv)
    try:
        ok = (
            offline_smoke_test()
            if args.offline
            else live_smoke_test(args.host_alias)
        )
    except Exception as error:
        print(f"Smoke test failed safely: {type(error).__name__}")
        return 1
    print(f"{'Offline' if args.offline else 'Live'} smoke test: "
          f"{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
