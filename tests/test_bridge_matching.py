"""Tests for spool lookup logic in bridge.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import bridge


def _make_mock_response(spools):
    """Return a mock requests.Response whose .json() gives spool list."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = spools
    mock_resp.raise_for_status.return_value = None
    return mock_resp


# ---------------------------------------------------------------------------
# find_spool_by_color_material
# ---------------------------------------------------------------------------

@patch("bridge.requests.get")
def test_find_by_color_material_exact(mock_get):
    """Exact color + material match returns the spool."""
    spool = {"id": 1, "filament": {"material": "PLA", "color_hex": "ff0000", "name": "PLA Red"}, "extra": {}}
    mock_get.return_value = _make_mock_response([spool])

    result = bridge.find_spool_by_color_material("ff0000", "PLA")
    assert result is not None
    assert result["id"] == 1


@patch("bridge.requests.get")
def test_find_by_color_material_prefix(mock_get):
    """'PLA Basic' in Spoolman matches a 'PLA' query (prefix match)."""
    spool = {"id": 2, "filament": {"material": "PLA Basic", "color_hex": "00ff00", "name": "PLA Basic Green"}, "extra": {}}
    mock_get.return_value = _make_mock_response([spool])

    result = bridge.find_spool_by_color_material("00ff00", "PLA")
    assert result is not None
    assert result["id"] == 2


@patch("bridge.requests.get")
def test_find_by_color_material_strip_ff(mock_get):
    """Input color 'FF0000FF' should match spool with 'ff0000' after stripping trailing 'ff'."""
    spool = {"id": 3, "filament": {"material": "PETG", "color_hex": "ff0000", "name": "PETG Red"}, "extra": {}}
    mock_get.return_value = _make_mock_response([spool])

    result = bridge.find_spool_by_color_material("FF0000FF", "PETG")
    assert result is not None
    assert result["id"] == 3


@patch("bridge.requests.get")
def test_find_by_color_material_no_match(mock_get):
    """Returns None when no spool matches color + material."""
    spool = {"id": 4, "filament": {"material": "ABS", "color_hex": "0000ff", "name": "ABS Blue"}, "extra": {}}
    mock_get.return_value = _make_mock_response([spool])

    result = bridge.find_spool_by_color_material("ff0000", "PLA")
    assert result is None


@patch("bridge.requests.get")
def test_find_by_color_material_multiple(mock_get):
    """When multiple spools match, returns the first one."""
    spool_a = {"id": 5, "filament": {"material": "PLA", "color_hex": "ff0000", "name": "PLA Red A"}, "extra": {}}
    spool_b = {"id": 6, "filament": {"material": "PLA", "color_hex": "ff0000", "name": "PLA Red B"}, "extra": {}}
    mock_get.return_value = _make_mock_response([spool_a, spool_b])

    result = bridge.find_spool_by_color_material("ff0000", "PLA")
    assert result is not None
    assert result["id"] == 5


# ---------------------------------------------------------------------------
# find_spool_by_tag
# ---------------------------------------------------------------------------

@patch("bridge.requests.get")
def test_find_by_tag_found(mock_get):
    """Finds a spool by its RFID tag UID stored in extra.tag."""
    spool = {
        "id": 7,
        "filament": {"material": "PLA", "color_hex": "123456", "name": "Tagged Spool"},
        "extra": {"tag": '"ABCD1234"'},
    }
    mock_get.return_value = _make_mock_response([spool])

    result = bridge.find_spool_by_tag("ABCD1234")
    assert result is not None
    assert result["id"] == 7


@patch("bridge.requests.get")
def test_find_by_tag_not_found(mock_get):
    """Returns None when no spool has the given tag UID."""
    spool = {
        "id": 8,
        "filament": {"material": "PLA", "color_hex": "abcdef", "name": "Other Spool"},
        "extra": {"tag": '"DEADBEEF"'},
    }
    mock_get.return_value = _make_mock_response([spool])

    result = bridge.find_spool_by_tag("NOTFOUND")
    assert result is None


@patch("bridge.requests.get")
def test_find_by_tag_empty(mock_get):
    """Returns None immediately for empty or None tag_uid without hitting the API."""
    mock_get.return_value = _make_mock_response([])

    assert bridge.find_spool_by_tag("") is None
    assert bridge.find_spool_by_tag(None) is None
    mock_get.assert_not_called()


@patch("bridge.requests.get")
def test_find_by_tag2(mock_get):
    """Finds a spool by its second RFID tag stored in extra.tag2."""
    spool = {
        "id": 10,
        "filament": {"material": "PLA", "color_hex": "ffffff", "name": "PLA White"},
        "extra": {"tag": '"AAAA1111"', "tag2": '"BBBB2222"'},
    }
    mock_get.return_value = _make_mock_response([spool])
    result = bridge.find_spool_by_tag("BBBB2222")
    assert result is not None
    assert result["id"] == 10


@patch("bridge.requests.get")
def test_find_by_tag2_tray_uuid(mock_get):
    """Finds a spool when tray_uuid matches tag2."""
    spool = {
        "id": 11,
        "filament": {"material": "PLA", "color_hex": "000000", "name": "PLA Black"},
        "extra": {"tag": '"CCCC3333"', "tag2": '"DDDD4444"'},
    }
    mock_get.return_value = _make_mock_response([spool])
    result = bridge.find_spool_by_tag("NOTFOUND", tray_uuid="DDDD4444")
    assert result is not None
    assert result["id"] == 11
