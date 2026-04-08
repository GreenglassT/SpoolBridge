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
    with patch("bridge.sm_get", return_value=[]):
        with patch("bridge.requests.patch", side_effect=Exception("network error")):
            # Should not raise — function has try/except
            bridge.update_spool_tray(1, "A1")


def test_update_spool_tray_evicts_existing():
    """update_spool_tray should clear active_tray from other spools in the same tray."""
    other_spool = {
        "id": 99, "extra": {"active_tray": '"A2"'},
        "filament": {"name": "Old"}, "remaining_weight": 500,
    }
    target_spool = {
        "id": 5, "extra": {"active_tray": '"A1"'},
        "filament": {"name": "New"}, "remaining_weight": 800,
    }
    all_spools = [other_spool, target_spool]

    patches = []
    def mock_patch(endpoint, data):
        patches.append((endpoint, data))

    with patch("bridge.sm_patch", side_effect=mock_patch):
        bridge.update_spool_tray(5, "A2", all_spools=all_spools)

    # Should have evicted spool #99 from A2, then assigned spool #5 to A2
    assert len(patches) == 2
    assert patches[0] == ("spool/99", {"extra": {"active_tray": '""'}})
    assert patches[1][0] == "spool/5"
    assert '"A2"' in patches[1][1]["extra"]["active_tray"]


def test_update_spool_tray_no_eviction_needed():
    """update_spool_tray should not evict when no other spool occupies the tray."""
    target_spool = {
        "id": 5, "extra": {"active_tray": '""'},
        "filament": {"name": "New"}, "remaining_weight": 800,
    }

    patches = []
    def mock_patch(endpoint, data):
        patches.append((endpoint, data))

    with patch("bridge.sm_patch", side_effect=mock_patch):
        bridge.update_spool_tray(5, "A1", all_spools=[target_spool])

    # Only the assignment, no eviction
    assert len(patches) == 1
    assert patches[0][0] == "spool/5"


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
