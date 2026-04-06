import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from server import _read_jsonl
from config import MATERIAL_MAP, DEFAULT_TEMPS


def test_read_jsonl_empty(tmp_path):
    result = _read_jsonl(tmp_path / "nonexistent.json")
    assert result == []


def test_read_jsonl_with_data(tmp_path):
    f = tmp_path / "data.jsonl"
    records = [{"id": 1}, {"id": 2}, {"id": 3}]
    f.write_text("\n".join(json.dumps(r) for r in records))
    result = _read_jsonl(f)
    assert len(result) == 3
    assert result == list(reversed(records))


def test_read_jsonl_limit(tmp_path):
    f = tmp_path / "data.jsonl"
    records = [{"id": 1}, {"id": 2}, {"id": 3}]
    f.write_text("\n".join(json.dumps(r) for r in records))
    result = _read_jsonl(f, limit=2)
    assert len(result) == 2


def test_material_map_coverage():
    assert MATERIAL_MAP["pla"] == "PLA"
    assert MATERIAL_MAP["petg"] == "PETG"
    assert MATERIAL_MAP["petg hf"] == "PETG"
    assert MATERIAL_MAP["abs"] == "ABS"
    assert MATERIAL_MAP["tpu"] == "TPU"
    assert MATERIAL_MAP["pla-cf"] == "PLA-CF"
    assert MATERIAL_MAP["petg-cf"] == "PETG-CF"


def test_default_temps_coverage():
    for mat_type in set(MATERIAL_MAP.values()):
        assert mat_type in DEFAULT_TEMPS, f"{mat_type} missing from DEFAULT_TEMPS"
        low, high = DEFAULT_TEMPS[mat_type]
        assert low < high, f"Expected low < high for {mat_type}, got {low} >= {high}"
