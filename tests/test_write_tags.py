import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from write_tags import spool_to_openspool_json, MATERIAL_MAP, DEFAULT_TEMPS


def _make_spool(material="PLA", color_hex="ff0000", vendor_name="Bambu Lab", extruder_temp=None):
    spool = {
        "id": 1,
        "filament": {
            "material": material,
            "color_hex": color_hex,
            "vendor": {"name": vendor_name},
        },
    }
    if extruder_temp is not None:
        spool["filament"]["settings_extruder_temp"] = extruder_temp
    return spool


def test_spool_to_openspool_json_basic():
    spool = _make_spool(material="PLA", color_hex="ff0000", vendor_name="Bambu Lab")
    result = spool_to_openspool_json(spool)
    assert result["protocol"] == "openspool"
    assert result["version"] == "1.0"
    assert result["type"] == "PLA"
    assert result["color_hex"] == "FF0000"
    assert result["brand"] == "Bambu Lab"
    assert result["min_temp"] == "190"
    assert result["max_temp"] == "220"


def test_spool_to_openspool_json_material_mapping():
    spool = _make_spool(material="PETG HF")
    result = spool_to_openspool_json(spool)
    assert result["type"] == MATERIAL_MAP.get("petg hf")
    assert result["type"] == "PETG"


def test_spool_to_openspool_json_custom_temp():
    spool = _make_spool(material="PLA", extruder_temp=210)
    result = spool_to_openspool_json(spool)
    assert result["min_temp"] == "200"
    assert result["max_temp"] == "220"


def test_spool_to_openspool_json_default_temp():
    spool = _make_spool(material="ABS")
    result = spool_to_openspool_json(spool)
    min_temp, max_temp = DEFAULT_TEMPS["ABS"]
    assert result["min_temp"] == str(min_temp)
    assert result["max_temp"] == str(max_temp)
    assert result["min_temp"] == "240"
    assert result["max_temp"] == "270"


def test_spool_to_openspool_json_unknown_material():
    spool = _make_spool(material="SuperPlastic")
    result = spool_to_openspool_json(spool)
    assert result["type"] == "SUPERPLASTIC"
