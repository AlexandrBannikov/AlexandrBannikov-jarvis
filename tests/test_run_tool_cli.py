"""Tests for the run_tool command-line interface."""

import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest


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


def test_remote_cli_passes_only_named_parameters(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from app.tools.result import ToolResult
    from scripts import run_tool

    manager = Mock()
    manager.execute.return_value = ToolResult(
        True, "remote_service_status", {}, "ok", 1, None
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_tool.py",
            "remote_service_status",
            "--host",
            "crypto",
            "--service",
            "safe.service",
        ],
    )
    with patch.object(
        run_tool, "create_default_tool_manager", return_value=manager
    ):
        assert run_tool.main() == 0

    manager.execute.assert_called_once_with(
        "remote_service_status",
        host_alias="crypto",
        service_name="safe.service",
    )
    assert '"success": true' in capsys.readouterr().out


def test_cli_has_no_shell_command_parameter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import run_tool

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_tool.py", "remote_system_info", "--host", "crypto", "--command", "id"],
    )
    with pytest.raises(SystemExit):
        run_tool.main()
