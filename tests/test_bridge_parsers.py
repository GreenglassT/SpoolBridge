"""
Tests for pure MQTT message parsing functions in bridge.py.
No mocking needed — just construct payloads and call the functions.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from bridge import parse_vt_tray, parse_ams_trays, parse_ams_environment, parse_print_finish


# ============================================================
# parse_vt_tray
# ============================================================

def test_parse_vt_tray_valid():
    """Full vt_tray payload extracts type, color, and temperature range."""
    msg_data = {
        "print": {
            "vt_tray": {
                "tray_type": "PETG",
                "tray_color": "FF0000",
                "nozzle_temp_min": 220,
                "nozzle_temp_max": 240,
            }
        }
    }
    result = parse_vt_tray(msg_data)
    assert result is not None
    assert result["type"] == "PETG"
    assert result["color"] == "FF0000"
    assert result["nozzle_min"] == 220
    assert result["nozzle_max"] == 240


def test_parse_vt_tray_empty():
    """Returns None for missing vt_tray or when tray_type is absent."""
    # Missing vt_tray entirely
    assert parse_vt_tray({"print": {}}) is None

    # vt_tray present but empty
    assert parse_vt_tray({"print": {"vt_tray": {}}}) is None

    # vt_tray present but no tray_type
    assert parse_vt_tray({"print": {"vt_tray": {"tray_color": "FF0000"}}}) is None

    # No print key at all
    assert parse_vt_tray({}) is None


def test_parse_vt_tray_color_truncation():
    """Color value 'FF0000FF' (8 chars with alpha) is truncated to 'FF0000'."""
    msg_data = {
        "print": {
            "vt_tray": {
                "tray_type": "PLA",
                "tray_color": "FF0000FF",
                "nozzle_temp_min": 190,
                "nozzle_temp_max": 210,
            }
        }
    }
    result = parse_vt_tray(msg_data)
    assert result is not None
    assert result["color"] == "FF0000"


# ============================================================
# parse_ams_trays
# ============================================================

def test_parse_ams_trays_single_unit():
    """One AMS unit with 4 trays produces slot keys like 'A0_0', 'A0_1', etc."""
    msg_data = {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FF0000", "tag_uid": "abc123", "remain": 80},
                            {"id": "1", "tray_type": "PETG", "tray_color": "00FF00", "tag_uid": "def456", "remain": 60},
                            {"id": "2", "tray_type": "ABS", "tray_color": "0000FF", "tag_uid": "ghi789", "remain": 40},
                            {"id": "3", "tray_type": "TPU", "tray_color": "FFFF00", "tag_uid": "jkl012", "remain": 20},
                        ]
                    }
                ]
            }
        }
    }
    result = parse_ams_trays(msg_data)
    assert len(result) == 4
    assert "A0_0" in result
    assert "A0_1" in result
    assert "A0_2" in result
    assert "A0_3" in result
    assert result["A0_0"]["type"] == "PLA"
    assert result["A0_1"]["type"] == "PETG"


def test_parse_ams_trays_empty_slots():
    """Empty slots (tray_type='') are included with type=''."""
    msg_data = {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FF0000", "tag_uid": "abc", "remain": 90},
                            {"id": "1", "tray_type": "", "tray_color": "", "tag_uid": "", "remain": -1},
                            {"id": "2", "tray_type": "PETG", "tray_color": "00FF00", "tag_uid": "xyz", "remain": 50},
                            {"id": "3", "tray_type": "", "tray_color": "", "tag_uid": "", "remain": -1},
                        ]
                    }
                ]
            }
        }
    }
    result = parse_ams_trays(msg_data)
    assert len(result) == 4
    assert result["A0_0"]["type"] == "PLA"
    assert result["A0_1"]["type"] == ""
    assert result["A0_2"]["type"] == "PETG"
    assert result["A0_3"]["type"] == ""


def test_parse_ams_trays_multi_unit():
    """Multiple AMS units produce 'A0_0', 'A1_0', etc. with the correct unit prefix."""
    msg_data = {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {"id": "0", "tray_type": "PLA", "tray_color": "FF0000", "tag_uid": "aaa", "remain": 100},
                        ]
                    },
                    {
                        "id": "1",
                        "tray": [
                            {"id": "0", "tray_type": "PETG", "tray_color": "00FF00", "tag_uid": "bbb", "remain": 75},
                        ]
                    },
                ]
            }
        }
    }
    result = parse_ams_trays(msg_data)
    assert len(result) == 2
    assert "A0_0" in result
    assert "A1_0" in result
    assert result["A0_0"]["type"] == "PLA"
    assert result["A1_0"]["type"] == "PETG"


# ============================================================
# parse_ams_environment
# ============================================================

def test_parse_ams_environment_present():
    """Extracts humidity and temp from the first AMS unit."""
    msg_data = {
        "print": {
            "ams": {
                "ams": [
                    {"id": "0", "humidity": "45", "temp": "25.5", "tray": []}
                ]
            }
        }
    }
    result = parse_ams_environment(msg_data)
    assert result is not None
    assert result["humidity"] == "45"
    assert result["temp"] == "25.5"


def test_parse_ams_environment_missing():
    """Returns None when the AMS list is empty or absent."""
    # Empty AMS list
    assert parse_ams_environment({"print": {"ams": {"ams": []}}}) is None

    # No ams key at all
    assert parse_ams_environment({"print": {}}) is None

    # No print key
    assert parse_ams_environment({}) is None


# ============================================================
# parse_print_finish
# ============================================================

def test_parse_print_finish_success():
    """FINISH state with weight returns status 'finish' and grams used."""
    msg_data = {
        "print": {
            "gcode_state": "FINISH",
            "total_weight": 42.5,
        }
    }
    result = parse_print_finish(msg_data)
    assert result is not None
    assert result["status"] == "finish"
    assert result["used_grams"] == 42.5


def test_parse_print_finish_failed():
    """FAILED state returns status 'failed'."""
    msg_data = {
        "print": {
            "gcode_state": "FAILED",
            "total_weight": 10.0,
        }
    }
    result = parse_print_finish(msg_data)
    assert result is not None
    assert result["status"] == "failed"
    assert result["used_grams"] == 10.0


def test_parse_print_finish_no_weight():
    """FINISH state with zero (or absent) weight returns used_grams=0."""
    msg_data = {
        "print": {
            "gcode_state": "FINISH",
            "total_weight": 0,
        }
    }
    result = parse_print_finish(msg_data)
    assert result is not None
    assert result["status"] == "finish"
    assert result["used_grams"] == 0


def test_parse_print_finish_running():
    """Non-finish states (RUNNING, IDLE, PREPARE, etc.) return None."""
    for state in ("RUNNING", "IDLE", "PREPARE", "PAUSED", ""):
        msg_data = {"print": {"gcode_state": state, "total_weight": 100}}
        assert parse_print_finish(msg_data) is None, f"Expected None for state={state!r}"
