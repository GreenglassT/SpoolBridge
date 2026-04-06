"""
Contract tests verifying bridge.py file outputs are correctly consumed by server.py.

These tests ensure the file format written by bridge functions matches what the
server's reader functions expect.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge
from server import _read_jsonl


def test_bridge_state_format(tmp_scripts_dir):
    """write_bridge_state() produces a file with keys the server expects."""
    bridge.ams_environment = {"humidity": "3", "temp": "22.1"}
    bridge.unknown_spools = {
        "A0_1": {"type": "PLA", "color": "ff0000", "tag_uid": "AABBCCDD", "tray_num": 2, "tray": "A2"},
    }

    bridge.write_bridge_state()

    state_path = tmp_scripts_dir / "bridge_state.json"
    assert state_path.exists()

    state = json.loads(state_path.read_text())

    # Keys expected by the server's /api/printer-status endpoint
    assert "connected" in state
    assert isinstance(state["connected"], bool)
    assert state["connected"] is True

    assert "ams_environment" in state
    assert isinstance(state["ams_environment"], dict)

    assert "unknown_spools" in state
    assert isinstance(state["unknown_spools"], list)


def test_activity_json_format(tmp_scripts_dir):
    """log_event() writes entries that _read_jsonl() returns in reversed order."""
    bridge.log_event("tray_load", tray="A1", spool_id=5, spool_name="PLA Red", detail="tag ABC")
    bridge.log_event("print_done", spool_id=5, spool_name="PLA Red", detail="finish, 20g")

    events = _read_jsonl(tmp_scripts_dir / "activity.json")

    assert len(events) == 2
    # _read_jsonl reverses the file, so the last-written event comes first
    assert events[0]["type"] == "print_done"
    assert events[1]["type"] == "tray_load"


def test_prints_json_format(tmp_scripts_dir):
    """log_print() writes an entry that _read_jsonl() returns with correct fields."""
    bridge.log_print(
        spool_id=3,
        spool_name="PETG White",
        material="PETG",
        color_hex="ffffff",
        grams_used=30.0,
        status="finish",
    )

    entries = _read_jsonl(tmp_scripts_dir / "prints.json")

    assert len(entries) == 1
    entry = entries[0]
    assert entry["spool_id"] == 3
    assert entry["spool_name"] == "PETG White"
    assert entry["material"] == "PETG"
    assert entry["color_hex"] == "ffffff"
    assert entry["grams_used"] == 30.0
    assert entry["status"] == "finish"
    assert "ts" in entry
