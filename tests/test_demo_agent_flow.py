"""The documented agent demo must remain entirely local and deterministic."""

from pathlib import Path
import subprocess
import sys


def test_fake_agent_demo_completes_without_network() -> None:
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "scripts/demo_agent_flow.py"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Пользователь →" in completed.stdout
    assert "function_call: system_info" in completed.stdout
    assert "function_call_output" in completed.stdout
    assert "Jarvis →" in completed.stdout
