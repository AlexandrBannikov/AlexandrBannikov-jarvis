"""Tests for the run_tool command-line interface."""

import json
from pathlib import Path
import subprocess
import sys


def test_run_tool_cli_outputs_json() -> None:
    project_root = Path(__file__).resolve().parents[1]

    completed = subprocess.run(
        [sys.executable, "scripts/run_tool.py", "system_info"],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    output = json.loads(completed.stdout)

    assert output["success"] is True
    assert output["tool"] == "system_info"
    assert "hostname" in output["data"]
    assert output["error"] is None
