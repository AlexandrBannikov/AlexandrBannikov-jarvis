"""Tests for explicit smoke-test modes."""

from unittest.mock import Mock

import pytest

from scripts import smoke_test


def test_offline_smoke_test_passes_without_live_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Mock(side_effect=AssertionError("live mode must not run"))
    monkeypatch.setattr(smoke_test, "live_smoke_test", live)

    assert smoke_test.main(["--offline"]) == 0

    live.assert_not_called()


def test_live_smoke_test_does_not_run_without_live_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Mock(side_effect=AssertionError("live mode must not run"))
    monkeypatch.setattr(smoke_test, "live_smoke_test", live)

    with pytest.raises(SystemExit):
        smoke_test.main([])

    live.assert_not_called()
