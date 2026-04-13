"""
Tests for MQTT callback functions in bridge.py:
- on_connect: subscribes to topic and publishes pushall
- on_message: dispatches to handlers based on message content
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge


# ============================================================
# Helpers
# ============================================================

def make_msg(data, topic="device/TEST_SERIAL/report"):
    """Create a mock MQTT message with a JSON payload."""
    msg = MagicMock()
    msg.payload = json.dumps(data).encode()
    msg.topic = topic
    return msg


# ============================================================
# on_connect
# ============================================================

def test_on_connect_subscribes(tmp_scripts_dir):
    """on_connect with rc=0 subscribes to report + request topics and publishes pushall."""
    bridge.PRINTER_SERIAL = "TEST_SERIAL"

    client = MagicMock()
    bridge.on_connect(client, None, {}, 0)

    topics = [call[0][0] for call in client.subscribe.call_args_list]
    assert "device/TEST_SERIAL/report" in topics
    assert "device/TEST_SERIAL/request" in topics
    client.publish.assert_called_once()
    publish_args = client.publish.call_args
    assert publish_args[0][0] == "device/TEST_SERIAL/request"
    payload = json.loads(publish_args[0][1])
    assert payload == {"pushing": {"command": "pushall"}}


def test_on_connect_failed_rc(tmp_scripts_dir):
    """on_connect with non-zero rc does not subscribe or publish."""
    bridge.PRINTER_SERIAL = "TEST_SERIAL"

    client = MagicMock()
    bridge.on_connect(client, None, {}, 1)

    client.subscribe.assert_not_called()
    client.publish.assert_not_called()


# ============================================================
# on_message
# ============================================================

def test_on_message_dispatches_vt(tmp_scripts_dir):
    """Message with vt_tray data triggers handle_vt_tray_change."""
    data = {
        "print": {
            "vt_tray": {
                "tray_type": "PETG",
                "tray_color": "FF0000",
                "nozzle_temp_min": 220,
                "nozzle_temp_max": 240,
            }
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_vt_tray_change") as mock_vt, \
         patch.object(bridge, "handle_ams_change") as mock_ams, \
         patch.object(bridge, "write_bridge_state"):
        bridge.on_message(client, None, make_msg(data))

    mock_vt.assert_called_once()
    called_arg = mock_vt.call_args[0][0]
    assert called_arg["type"] == "PETG"
    assert called_arg["color"] == "FF0000"
    mock_ams.assert_not_called()


def test_on_message_dispatches_ams(tmp_scripts_dir):
    """Message with AMS tray data triggers handle_ams_change."""
    data = {
        "print": {
            "ams": {
                "ams": [
                    {
                        "id": "0",
                        "tray": [
                            {
                                "id": "0",
                                "tray_type": "PLA",
                                "tray_color": "00FF00",
                                "tag_uid": "abc123",
                                "remain": 80,
                            }
                        ],
                    }
                ]
            }
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_vt_tray_change") as mock_vt, \
         patch.object(bridge, "handle_ams_change") as mock_ams, \
         patch.object(bridge, "write_bridge_state"):
        bridge.on_message(client, None, make_msg(data))

    mock_ams.assert_called_once()
    called_arg = mock_ams.call_args[0][0]
    assert "A0_0" in called_arg
    assert called_arg["A0_0"]["type"] == "PLA"
    mock_vt.assert_not_called()


def test_on_message_state_transition(tmp_scripts_dir):
    """Transitioning from RUNNING to FINISH fires handle_print_finish."""
    bridge.last_gcode_state = "RUNNING"

    data = {
        "print": {
            "gcode_state": "FINISH",
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_print_finish") as mock_finish, \
         patch.object(bridge, "write_bridge_state"):
        bridge.on_message(client, None, make_msg(data))

    mock_finish.assert_called_once()
    called_arg = mock_finish.call_args[0][0]
    assert called_arg["status"] == "finish"


def test_on_message_no_double_fire(tmp_scripts_dir):
    """Already in FINISH state does not re-fire handle_print_finish."""
    bridge.last_gcode_state = "FINISH"

    data = {
        "print": {
            "gcode_state": "FINISH",
            "total_weight": 25.0,
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_print_finish") as mock_finish, \
         patch.object(bridge, "write_bridge_state"):
        bridge.on_message(client, None, make_msg(data))

    mock_finish.assert_not_called()


# ============================================================
# Request topic (OpenSpool tag reads)
# ============================================================

def test_parse_request_filament_setting_valid():
    """Valid ams_filament_setting with ams_id=255 returns parsed dict."""
    data = {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": 255,
            "tray_type": "PLA",
            "tray_color": "ff0000ff",
            "nozzle_temp_min": 190,
            "nozzle_temp_max": 230,
        }
    }
    result = bridge.parse_request_filament_setting(data)
    assert result is not None
    assert result["type"] == "PLA"
    assert result["color"] == "FF0000"
    assert result["nozzle_min"] == 190
    assert result["nozzle_max"] == 230


def test_parse_request_filament_setting_wrong_command():
    """Non-matching command returns None."""
    data = {"print": {"command": "pushall", "ams_id": 255, "tray_type": "PLA"}}
    assert bridge.parse_request_filament_setting(data) is None


def test_parse_request_filament_setting_ams_tray():
    """ams_id=0 (AMS tray, not external) returns None."""
    data = {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": 0,
            "tray_type": "PLA",
            "tray_color": "ff0000ff",
        }
    }
    assert bridge.parse_request_filament_setting(data) is None


def test_request_topic_triggers_vt_handler(tmp_scripts_dir):
    """ams_filament_setting on request topic calls handle_vt_tray_change."""
    bridge._last_own_publish_time = 0
    data = {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": 255,
            "tray_type": "PETG",
            "tray_color": "00ff00ff",
            "nozzle_temp_min": 220,
            "nozzle_temp_max": 250,
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_vt_tray_change") as mock_vt:
        bridge.on_message(client, None, make_msg(data, topic="device/TEST_SERIAL/request"))

    mock_vt.assert_called_once()
    called_arg = mock_vt.call_args[0][0]
    assert called_arg["type"] == "PETG"
    assert called_arg["color"] == "00FF00"


def test_request_topic_ignores_own_publish(tmp_scripts_dir):
    """Messages within 2s of our own publish are ignored (echo suppression)."""
    import time as _time
    bridge._last_own_publish_time = _time.time()
    data = {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": 255,
            "tray_type": "PLA",
            "tray_color": "ffffffff",
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_vt_tray_change") as mock_vt:
        bridge.on_message(client, None, make_msg(data, topic="device/TEST_SERIAL/request"))

    mock_vt.assert_not_called()


def test_request_topic_ignores_ams_tray(tmp_scripts_dir):
    """ams_id=0 on request topic does not trigger handle_vt_tray_change."""
    bridge._last_own_publish_time = 0
    data = {
        "print": {
            "command": "ams_filament_setting",
            "ams_id": 0,
            "tray_type": "PLA",
            "tray_color": "ffffffff",
        }
    }
    client = MagicMock()
    with patch.object(bridge, "handle_vt_tray_change") as mock_vt:
        bridge.on_message(client, None, make_msg(data, topic="device/TEST_SERIAL/request"))

    mock_vt.assert_not_called()


def test_on_message_bad_json(tmp_scripts_dir):
    """Non-JSON payload is silently ignored; write_bridge_state is not called."""
    msg = MagicMock()
    msg.payload = b"not valid json {"

    client = MagicMock()
    with patch.object(bridge, "write_bridge_state") as mock_write, \
         patch.object(bridge, "handle_vt_tray_change") as mock_vt, \
         patch.object(bridge, "handle_ams_change") as mock_ams, \
         patch.object(bridge, "handle_print_finish") as mock_finish:
        bridge.on_message(client, None, msg)

    mock_write.assert_not_called()
    mock_vt.assert_not_called()
    mock_ams.assert_not_called()
    mock_finish.assert_not_called()
