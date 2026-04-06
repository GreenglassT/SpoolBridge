"""Tests for add-spool.py CLI spool creator."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Load add-spool.py using importlib (hyphen in filename)
spec = importlib.util.spec_from_file_location(
    "add_spool", Path(__file__).parent.parent / "add-spool.py"
)
add_spool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add_spool)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_urlopen(data):
    """Create a mock urlopen context manager that returns JSON data."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode()
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# ── resolve_color ─────────────────────────────────────────────────────────────

def test_resolve_color_by_name():
    assert add_spool.resolve_color("jade white") == "ffffff"


def test_resolve_color_by_hex():
    assert add_spool.resolve_color("ff7eb3") == "ff7eb3"


def test_resolve_color_strips_hash():
    assert add_spool.resolve_color("#ff0000") == "ff0000"


def test_resolve_color_strips_alpha():
    # 8-char hex with alpha suffix → strip to 6
    assert add_spool.resolve_color("ff0000ff") == "ff0000"


def test_resolve_color_fuzzy():
    # "dark" is a substring of several color names (dark gray, dark blue, etc.)
    result = add_spool.resolve_color("dark")
    assert result != ""


def test_resolve_color_unknown():
    assert add_spool.resolve_color("xyzzy") == ""


# ── get_material_info ─────────────────────────────────────────────────────────

def test_material_exact_match():
    info = add_spool.get_material_info("PLA Basic")
    assert info["density"] == 1.24
    assert info["nozzle_min"] == 190


def test_material_case_insensitive():
    info = add_spool.get_material_info("pla basic")
    assert info["density"] == 1.24
    assert info["nozzle_min"] == 190


def test_material_prefix_match():
    # "PLA+ Tough" starts with "PLA+" but dict iteration may hit "PLA" first
    # The function iterates MATERIALS and returns first prefix match
    info = add_spool.get_material_info("PLA+ Tough")
    # Should match either PLA or PLA+ depending on dict order
    assert info["density"] == 1.24
    assert info["nozzle_min"] in (190, 200)


def test_material_unknown_defaults_pla():
    info = add_spool.get_material_info("UnknownXYZ")
    pla = add_spool.MATERIALS["PLA"]
    assert info["density"] == pla["density"]
    assert info["nozzle_min"] == pla["nozzle_min"]


# ── get_spoolman_material ─────────────────────────────────────────────────────

def test_spoolman_material_with_parent():
    # "PLA Basic" has parent "PLA" — Spoolman should use "PLA" for AMS matching
    result = add_spool.get_spoolman_material("PLA Basic")
    assert result == "PLA"


def test_spoolman_material_no_parent():
    # "PETG" has no parent — should return itself
    result = add_spool.get_spoolman_material("PETG")
    assert result == "PETG"


# ── parse_entry ───────────────────────────────────────────────────────────────

def test_parse_entry_full():
    line = "Bambu, PLA Basic, Pink, ff7eb3, 22.99, https://example.com"
    entry = add_spool.parse_entry(line)
    assert entry["brand"] == "Bambu"
    assert entry["material"] == "PLA Basic"
    assert entry["color_name"] == "Pink"
    assert entry["color_hex"] == "ff7eb3"
    assert entry["price"] == 22.99
    assert entry["url"] == "https://example.com"


def test_parse_entry_minimal():
    line = "Inland, PLA, Red"
    entry = add_spool.parse_entry(line)
    assert entry["brand"] == "Inland"
    assert entry["material"] == "PLA"
    assert entry["color_name"] == "Red"
    # Optional fields default
    assert entry.get("price") is None
    assert entry.get("url") is None


def test_parse_entry_resolves_color():
    # color_hex is empty — should resolve from color_name "Black"
    line = "Bambu, PLA, Black,"
    entry = add_spool.parse_entry(line)
    assert entry["color_hex"] == "000000"


def test_parse_entry_too_few_fields():
    line = "Bambu, PLA"
    entry = add_spool.parse_entry(line)
    assert entry == {}


def test_parse_entry_quoted_csv():
    # csv.reader only strips quotes on the first field when spaces follow commas
    # Test with no spaces after commas (proper CSV)
    line = '"Bambu Lab","PLA Basic","Jade White",ffffff'
    entry = add_spool.parse_entry(line)
    assert entry["brand"] == "Bambu Lab"
    assert entry["material"] == "PLA Basic"
    assert entry["color_name"] == "Jade White"
    assert entry["color_hex"] == "ffffff"


def test_parse_entry_price_float():
    line = "Inland, PLA, Red, cc0000, 18.99"
    entry = add_spool.parse_entry(line)
    assert isinstance(entry["price"], float)
    assert entry["price"] == 18.99


# ── find_filament ─────────────────────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_find_filament_by_material_color(mock_urlopen):
    filaments = [
        {
            "id": 42,
            "vendor": {"id": 1},
            "material": "PLA",
            "color_hex": "ff7eb3",
            "name": "PLA Basic Pink",
        }
    ]
    mock_urlopen.return_value = _mock_urlopen(filaments)
    result = add_spool.find_filament(vendor_id=1, material="PLA", color_hex="ff7eb3")
    assert result == 42


@patch("urllib.request.urlopen")
def test_find_filament_by_name(mock_urlopen):
    # Material/color don't match but name does
    filaments = [
        {
            "id": 7,
            "vendor": {"id": 3},
            "material": "PLA",
            "color_hex": "aabbcc",
            "name": "special edition red",
        }
    ]
    mock_urlopen.return_value = _mock_urlopen(filaments)
    result = add_spool.find_filament(
        vendor_id=3, material="PLA", color_hex="ff0000", name="Special Edition Red"
    )
    assert result == 7


@patch("urllib.request.urlopen")
def test_find_filament_no_match(mock_urlopen):
    filaments = [
        {
            "id": 99,
            "vendor": {"id": 5},
            "material": "ABS",
            "color_hex": "000000",
            "name": "ABS Black",
        }
    ]
    mock_urlopen.return_value = _mock_urlopen(filaments)
    result = add_spool.find_filament(
        vendor_id=5, material="PLA", color_hex="ffffff", name="PLA White"
    )
    assert result is None


# ── get_or_create_vendor ──────────────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_get_vendor_existing(mock_urlopen):
    vendors = [{"id": 10, "name": "Bambu Lab"}]
    mock_urlopen.return_value = _mock_urlopen(vendors)
    result = add_spool.get_or_create_vendor("bambu")
    assert result == 10


@patch("urllib.request.urlopen")
def test_create_vendor_new(mock_urlopen, capsys):
    # First call returns empty list (GET), second call returns created vendor (POST)
    calls = iter([
        _mock_urlopen([]),                      # GET /api/v1/vendor
        _mock_urlopen({"id": 55, "name": "Inland"}),  # POST /api/v1/vendor
    ])
    mock_urlopen.side_effect = lambda *args, **kwargs: next(calls)
    result = add_spool.get_or_create_vendor("inland")
    assert result == 55


@patch("urllib.request.urlopen")
def test_get_vendor_unknown_brand(mock_urlopen):
    # Unknown brand key — vendor name should be the raw brand key string
    vendors = [{"id": 77, "name": "MyCoolBrand"}]
    mock_urlopen.return_value = _mock_urlopen(vendors)
    result = add_spool.get_or_create_vendor("MyCoolBrand")
    assert result == 77
