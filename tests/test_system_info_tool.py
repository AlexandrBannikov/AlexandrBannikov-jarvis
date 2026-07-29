"""Tests for the built-in SystemInfoTool."""

from app.tools.system_info import SystemInfoTool


def test_system_info_metadata() -> None:
    tool = SystemInfoTool()

    assert tool.name == "system_info"
    assert tool.description == "Returns basic information about current host."
    assert tool.parameters()["additionalProperties"] is False


def test_system_info_returns_expected_fields() -> None:
    result = SystemInfoTool().execute()

    assert set(result) == {
        "hostname",
        "os",
        "python_version",
        "cpu_count",
        "architecture",
        "current_utc_time",
        "uptime_seconds",
        "pid",
    }
    assert isinstance(result["hostname"], str)
    assert isinstance(result["pid"], int)
    assert result["uptime_seconds"] >= 0
