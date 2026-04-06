"""Tests for bridge.py error resilience — verifies graceful failure handling."""

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge


# ---------------------------------------------------------------------------
# test_update_spool_tray_api_failure
# ---------------------------------------------------------------------------

def test_update_spool_tray_api_failure():
    """update_spool_tray should not raise when requests.patch raises Exception."""
    with patch("bridge.requests.patch", side_effect=Exception("network error")):
        # Should not raise — function has try/except
        bridge.update_spool_tray(1, "A1")


# ---------------------------------------------------------------------------
# test_update_spool_weight_api_failure
# ---------------------------------------------------------------------------

def test_update_spool_weight_api_failure():
    """update_spool_weight should not raise when requests.patch raises Exception."""
    with patch("bridge.sm_get", return_value={"remaining_weight": 1000}):
        with patch("bridge.requests.patch", side_effect=Exception("network error")):
            # Should not raise — function has try/except
            bridge.update_spool_weight(1, 50.0)


# ---------------------------------------------------------------------------
# test_find_spool_api_failure
# ---------------------------------------------------------------------------

def test_find_spool_api_failure():
    """find_spool_by_color_material should propagate exceptions from requests.get."""
    with patch("bridge.requests.get", side_effect=Exception("DNS failure")):
        with pytest.raises(Exception, match="DNS failure"):
            bridge.find_spool_by_color_material("ff0000", "PLA")


# ---------------------------------------------------------------------------
# test_log_event_write_failure
# ---------------------------------------------------------------------------

def test_log_event_write_failure(tmp_scripts_dir):
    """log_event should not raise when the activity.json directory is read-only."""
    dir_path = str(tmp_scripts_dir)
    os.chmod(dir_path, 0o444)
    try:
        # Should not raise — function has try/except
        bridge.log_event("test")
    finally:
        # Restore permissions so tmp_path cleanup doesn't fail
        os.chmod(dir_path, 0o755)


# ---------------------------------------------------------------------------
# test_write_bridge_state_failure
# ---------------------------------------------------------------------------

def test_write_bridge_state_failure(monkeypatch):
    """write_bridge_state should not raise when the target path is in a non-existent directory."""
    bad_path = Path("/nonexistent/directory/bridge_state.json")
    monkeypatch.setattr(bridge, "bridge_state_path", bad_path)
    # Should not raise — function has try/except
    bridge.write_bridge_state()


# ---------------------------------------------------------------------------
# test_on_disconnect_writes_state
# ---------------------------------------------------------------------------

def test_on_disconnect_writes_state(tmp_scripts_dir):
    """on_disconnect should write bridge_state.json with connected: False."""
    bridge.on_disconnect(None, None, 0)

    state_file = tmp_scripts_dir / "bridge_state.json"
    assert state_file.exists(), "bridge_state.json should be created by on_disconnect"

    state = json.loads(state_file.read_text())
    assert state.get("connected") is False
