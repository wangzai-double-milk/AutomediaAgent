"""Unit checks never use the live browser's persistent account restriction."""

import pytest


@pytest.fixture(autouse=True)
def isolated_account_access(monkeypatch, tmp_path, tmp_path_factory):
    from tools import xhs_account
    monkeypatch.setattr(xhs_account, "STATE_PATH", tmp_path / "account_access.json")
    from tools import xhs_test_control
    control_dir = tmp_path_factory.mktemp("isolated-test-control")
    monkeypatch.setattr(xhs_test_control, "STATE_PATH", control_dir / "test_control.json")
    from tools.xhs_publish import atomic_json
    # Existing transport contract tests exercise their legacy offline mode.
    atomic_json(xhs_test_control.STATE_PATH, {"version": 1, "enabled": False,
                "binding": None, "background_paused": False, "reservations": []})
