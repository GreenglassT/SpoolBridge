"""
Tests for file I/O functions in bridge.py:
  - log_event: appends JSON-lines to activity.json, caps at 200
  - log_print: appends JSON-lines to prints.json
  - write_bridge_state: writes bridge_state.json with current state
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge
from bridge import log_event, log_print, write_bridge_state


# ============================================================
# log_event
# ============================================================

def test_log_event_creates_file(tmp_scripts_dir):
    """First log_event call creates activity.json."""
    activity_path = tmp_scripts_dir / "activity.json"
    assert not activity_path.exists()

    log_event("spool_assigned", tray="A0_0", spool_id=1, spool_name="PLA Red", detail="test")

    assert activity_path.exists()
    lines = activity_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["type"] == "spool_assigned"
    assert record["tray"] == "A0_0"
    assert record["spool_id"] == 1
    assert record["spool_name"] == "PLA Red"
    assert record["detail"] == "test"
    assert "ts" in record


def test_log_event_appends(tmp_scripts_dir):
    """Multiple log_event calls append as separate JSON-lines."""
    log_event("spool_assigned", tray="A0_0", spool_id=1, spool_name="PLA Red")
    log_event("print_started", tray="A0_0", spool_id=1, spool_name="PLA Red", detail="started")
    log_event("print_finished", tray="A0_0", spool_id=1, spool_name="PLA Red", detail="done")

    activity_path = tmp_scripts_dir / "activity.json"
    lines = activity_path.read_text().strip().splitlines()
    assert len(lines) == 3

    types = [json.loads(line)["type"] for line in lines]
    assert types == ["spool_assigned", "print_started", "print_finished"]


def test_log_event_caps_at_200(tmp_scripts_dir):
    """After exceeding 250 entries, file is trimmed to last 200 lines."""
    activity_path = tmp_scripts_dir / "activity.json"

    # Pre-fill with 249 entries
    entries = []
    for i in range(249):
        entry = json.dumps({"ts": "2024-01-01T00:00:00", "type": "prefill", "tray": "",
                            "spool_id": None, "spool_name": "", "detail": str(i)})
        entries.append(entry)
    activity_path.write_text("\n".join(entries) + "\n")

    # Set the counter to match pre-filled lines
    bridge._jsonl_counts["activity.json"] = 249

    # Add 2 more events — second one pushes total to 251, triggering trim
    log_event("event_250", detail="250th")
    log_event("event_251", detail="251st")

    lines = activity_path.read_text().strip().splitlines()
    assert len(lines) == 200

    # The last line should be the most recent event
    last_record = json.loads(lines[-1])
    assert last_record["type"] == "event_251"

    # The trimmed content should be the tail of all 251 entries
    # First line should be the 52nd prefill entry (index 51, detail="51")
    first_record = json.loads(lines[0])
    assert first_record["detail"] == "51"


# ============================================================
# log_print
# ============================================================

def test_log_print_creates_file(tmp_scripts_dir):
    """First log_print call creates prints.json with correct fields."""
    prints_path = tmp_scripts_dir / "prints.json"
    assert not prints_path.exists()

    log_print(
        spool_id=42,
        spool_name="PETG Black",
        material="PETG",
        color_hex="000000",
        grams_used=15.3,
        status="finish",
    )

    assert prints_path.exists()
    lines = prints_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["spool_id"] == 42
    assert record["spool_name"] == "PETG Black"
    assert record["material"] == "PETG"
    assert record["color_hex"] == "000000"
    assert record["grams_used"] == 15.3
    assert record["status"] == "finish"
    assert "ts" in record


def test_log_print_caps_at_500(tmp_scripts_dir):
    """prints.json is trimmed when exceeding cap (500), keeps 80% (400)."""
    prints_path = tmp_scripts_dir / "prints.json"

    # Pre-fill with 499 entries
    entries = []
    for i in range(499):
        entry = json.dumps({"ts": "2024-01-01", "spool_id": i, "spool_name": "X",
                            "material": "PLA", "color_hex": "000000",
                            "grams_used": 1.0, "status": "finish",
                            "print_name": "", "duration": 0})
        entries.append(entry)
    prints_path.write_text("\n".join(entries) + "\n")

    # Add 2 more — second pushes to 501, triggering trim to 400 (80% of 500)
    log_print(spool_id=500, spool_name="A", material="PLA",
              color_hex="000000", grams_used=1.0, status="finish")
    log_print(spool_id=501, spool_name="B", material="PLA",
              color_hex="000000", grams_used=1.0, status="finish")

    lines = prints_path.read_text().strip().splitlines()
    assert len(lines) == 400

    # Last entry should be the most recent
    last = json.loads(lines[-1])
    assert last["spool_id"] == 501


# ============================================================
# write_bridge_state
# ============================================================

def test_write_bridge_state(tmp_scripts_dir):
    """write_bridge_state writes bridge_state.json with correct structure."""
    bridge.ams_environment = {"humidity": "42", "temp": "23.5"}
    bridge.unknown_spools = {
        "A0_2": {"type": "PLA", "color": "FF0000", "tray_num": 2, "tray": "A0_2"},
    }
    bridge.current_vt_tray = {"type": "PETG", "color": "0000FF"}

    write_bridge_state()

    state_path = tmp_scripts_dir / "bridge_state.json"
    assert state_path.exists()

    state = json.loads(state_path.read_text())
    assert state["connected"] is True
    assert "updated_at" in state
    assert state["ams_environment"] == {"humidity": "42", "temp": "23.5"}
    assert isinstance(state["unknown_spools"], list)
    assert len(state["unknown_spools"]) == 1
    assert state["unknown_spools"][0]["type"] == "PLA"
    assert state["current_vt_tray"] == {"type": "PETG", "color": "0000FF"}
