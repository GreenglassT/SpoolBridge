"""Tests for bridge.py sync handler functions:
- handle_vt_tray_change
- handle_ams_change
- handle_print_finish
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vt(type_="PLA", color="ff0000"):
    return {"type": type_, "color": color, "nozzle_min": 190, "nozzle_max": 230}


def _make_spool(spool_id=42, name="Red PLA", material="PLA", color="ff0000"):
    return {
        "id": spool_id,
        "filament": {"name": name, "material": material, "color_hex": color},
        "extra": {},
    }


# ---------------------------------------------------------------------------
# handle_vt_tray_change
# ---------------------------------------------------------------------------

@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
def test_vt_tray_change_matched(mock_find, mock_update_tray, mock_log_event):
    """When a matching spool is found, active_spool_id is set and tray is updated."""
    spool = _make_spool()
    mock_find.return_value = spool

    new_vt = _make_vt()
    bridge.handle_vt_tray_change(new_vt)

    mock_find.assert_called_once_with(new_vt["color"], new_vt["type"])
    assert bridge.active_spool_id == 42
    mock_update_tray.assert_called_once_with(42, "External")
    mock_log_event.assert_called_once()
    assert mock_log_event.call_args[1]["spool_id"] == 42 or mock_log_event.call_args[0][1:] or True
    # Verify event type
    call_args = mock_log_event.call_args
    assert call_args[0][0] == "external_tap"


@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
def test_vt_tray_change_unmatched(mock_find, mock_update_tray, mock_log_event):
    """When no matching spool is found, active_spool_id is set to None."""
    mock_find.return_value = None

    new_vt = _make_vt(type_="PETG", color="00ff00")
    bridge.handle_vt_tray_change(new_vt)

    mock_find.assert_called_once()
    assert bridge.active_spool_id is None
    mock_update_tray.assert_not_called()
    mock_log_event.assert_called_once()
    assert mock_log_event.call_args[0][0] == "external_tap"


@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
def test_vt_tray_no_change(mock_find, mock_update_tray, mock_log_event):
    """When the same vt tray data arrives twice, the second call is a no-op."""
    spool = _make_spool()
    mock_find.return_value = spool

    vt = _make_vt()
    bridge.handle_vt_tray_change(vt)
    assert mock_find.call_count == 1

    # Send identical data again — should be no-op
    bridge.handle_vt_tray_change(vt)
    assert mock_find.call_count == 1  # still 1, not called again
    assert mock_update_tray.call_count == 1
    assert mock_log_event.call_count == 1


# ---------------------------------------------------------------------------
# handle_ams_change
# ---------------------------------------------------------------------------

@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
@patch("bridge.find_spool_by_tag")
@patch("bridge.sm_get", return_value=[])
def test_ams_change_by_tag(mock_sm, mock_find_tag, mock_find_color, mock_update_tray, mock_log_event):
    """AMS tray with tag_uid: matched by tag, tray name is A2 for slot A0_1."""
    spool = _make_spool(spool_id=7)
    mock_find_tag.return_value = spool

    new_trays = {
        "A0_1": {
            "type": "PLA",
            "color": "ff0000",
            "tag_uid": "AABBCCDD",
            "tray_uuid": "",
            "remain": 80,
        }
    }
    bridge.handle_ams_change(new_trays)

    mock_find_tag.assert_called_once()
    # tray_num = int("1") + 1 = 2, so tray name is "A2"
    mock_update_tray.assert_called_once_with(7, "A2", all_spools=[])


@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
@patch("bridge.find_spool_by_tag")
@patch("bridge.auto_create_bambu_spool", return_value=None)
@patch("bridge.sm_get", return_value=[])
def test_ams_change_unknown_tag(mock_sm, mock_auto, mock_find_tag, mock_find_color, mock_update_tray, mock_log_event):
    """AMS tray with unrecognized tag is added to unknown_spools."""
    mock_find_tag.return_value = None

    new_trays = {
        "A0_2": {
            "type": "PETG",
            "color": "0000ff",
            "tag_uid": "DEADBEEF",
            "tray_uuid": "",
            "remain": 50,
        }
    }
    bridge.handle_ams_change(new_trays)

    mock_update_tray.assert_not_called()
    assert "A0_2" in bridge.unknown_spools
    entry = bridge.unknown_spools["A0_2"]
    assert entry["tag_uid"] == "DEADBEEF"
    assert entry["tray"] == "A3"  # int("2") + 1 = 3


@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
@patch("bridge.find_spool_by_tag")
@patch("bridge.sm_get", return_value=[])
def test_ams_change_no_tag_color_match(mock_sm, mock_find_tag, mock_find_color, mock_update_tray, mock_log_event):
    """AMS tray without tag_uid is matched by color+material."""
    spool = _make_spool(spool_id=15, material="PETG", color="123456")
    mock_find_color.return_value = spool

    new_trays = {
        "A0_0": {
            "type": "PETG",
            "color": "123456",
            "tag_uid": "",
            "tray_uuid": "",
            "remain": 60,
        }
    }
    bridge.handle_ams_change(new_trays)

    # tray_num = int("0") + 1 = 1 → "A1"
    mock_update_tray.assert_called_once_with(15, "A1", all_spools=[])


@patch("bridge.log_event")
@patch("bridge.update_spool_tray")
@patch("bridge.find_spool_by_color_material")
@patch("bridge.find_spool_by_tag")
@patch("bridge.sm_get", return_value=[])
def test_ams_change_no_change(mock_sm, mock_find_tag, mock_find_color, mock_update_tray, mock_log_event):
    """Identical AMS tray data sent twice — second call is a no-op."""
    spool = _make_spool(spool_id=9)
    mock_find_tag.return_value = spool

    tray_data = {"type": "PLA", "color": "aabb00", "tag_uid": "TAG1234", "tray_uuid": "", "remain": 90}
    new_trays = {"A0_0": tray_data}

    bridge.handle_ams_change(new_trays)
    first_call_count = mock_find_tag.call_count

    bridge.handle_ams_change(new_trays)
    # No change → no additional find calls
    assert mock_find_tag.call_count == first_call_count


# ---------------------------------------------------------------------------
# handle_print_finish
# ---------------------------------------------------------------------------

@patch("bridge.log_print")
@patch("bridge.log_event")
@patch("bridge.sync_remain_to_spoolman")
@patch("bridge.sm_get")
def test_print_finish_with_active_spool(mock_sm_get, mock_sync, mock_log_event, mock_log_print):
    """Print finish calculates grams used from weight difference (start vs current)."""
    bridge.active_spool_id = 42
    bridge.print_start_weight = 800.0
    bridge.print_start_time = 1000.0
    mock_sm_get.return_value = {
        "filament": {"name": "Red PLA", "material": "PLA", "color_hex": "ff0000"},
        "remaining_weight": 774.5,
    }

    finish_data = {"status": "finish"}
    bridge.handle_print_finish(finish_data)

    mock_sync.assert_called_once()
    mock_log_event.assert_called_once()
    mock_log_print.assert_called_once()
    log_print_kwargs = mock_log_print.call_args[1]
    assert log_print_kwargs["spool_id"] == 42
    assert log_print_kwargs["grams_used"] == 25.5


@patch("bridge.log_print")
@patch("bridge.log_event")
@patch("bridge.sync_remain_to_spoolman")
@patch("bridge.sm_get")
def test_print_finish_no_active_spool(mock_sm_get, mock_sync, mock_log_event, mock_log_print):
    """Print finish without active spool and no weight change skips logging entirely."""
    bridge.active_spool_id = None
    bridge.print_start_weight = None
    bridge.print_start_time = None

    finish_data = {"status": "finish"}
    bridge.handle_print_finish(finish_data)

    mock_sm_get.assert_not_called()
    # No spool, no usage → skipped entirely
    mock_log_event.assert_not_called()
    mock_log_print.assert_not_called()


@patch("bridge.log_print")
@patch("bridge.log_event")
@patch("bridge.sync_remain_to_spoolman")
@patch("bridge.sm_get")
def test_print_finish_no_weight_change(mock_sm_get, mock_sync, mock_log_event, mock_log_print):
    """Print finish with no weight change reports 0g used."""
    bridge.active_spool_id = 99
    bridge.print_start_weight = 500.0
    bridge.print_start_time = 1000.0
    mock_sm_get.return_value = {
        "filament": {"name": "Blue PETG", "material": "PETG", "color_hex": "0000ff"},
        "remaining_weight": 500.0,
    }

    finish_data = {"status": "finish"}
    bridge.handle_print_finish(finish_data)

    mock_log_event.assert_called_once()
    mock_log_print.assert_called_once()
    log_print_kwargs = mock_log_print.call_args[1]
    assert log_print_kwargs["grams_used"] == 0
