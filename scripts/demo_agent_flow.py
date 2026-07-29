#!/usr/bin/env python3
"""Demonstrate the agent loop with no OpenAI or SSH network access."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai.agent import JarvisAgent  # noqa: E402
from app.tools.manager import ToolManager  # noqa: E402
from app.tools.registry import ToolRegistry  # noqa: E402
from app.tools.system_info import SystemInfoTool  # noqa: E402


class FakeOpenAIProvider:
    def __init__(self) -> None:
        self.step = 0

    def create_response(self, input_items: object, **kwargs: object) -> object:
        del kwargs
        self.step += 1
        if self.step == 1:
            print("Fake OpenAI → function_call: system_info")
            return SimpleNamespace(
                id="fake-response-1",
                output=[
                    SimpleNamespace(
                        type="function_call",
                        call_id="fake-call-1",
                        name="system_info",
                        arguments="{}",
                    )
                ],
                output_text="",
            )
        print("ToolResult → function_call_output:", input_items)
        return SimpleNamespace(
            id="fake-response-2",
            output=[],
            output_text="Jarvis работает; локальная диагностика выполнена.",
        )


async def run_immediately(function, *args, **kwargs):
    return function(*args, **kwargs)


async def main() -> None:
    registry = ToolRegistry()
    registry.register(SystemInfoTool())
    agent = JarvisAgent(
        FakeOpenAIProvider(),
        ToolManager(registry),
        run_sync=run_immediately,
    )
    prompt = "Покажи информацию о сервере Jarvis"
    print("Пользователь →", prompt)
    print("Jarvis →", await agent.ask(prompt, user_id=123))


if __name__ == "__main__":
    asyncio.run(main())
