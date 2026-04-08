"""
Flask API Tests — proxy to a real Spoolman instance.

Requires Spoolman running at localhost:7912 (or SPOOLMAN_URL env var).
Run with: python3 -m pytest tests/test_server_api.py -v
"""

import json
import sys
from pathlib import Path

import pytest
import requests as http_requests

sys.path.insert(0, str(Path(__file__).parent.parent))

pytestmark = pytest.mark.spoolman


# ============================================================
# HELPERS
# ============================================================

def _spoolman_get(url, path):
    return http_requests.get(f"{url}{path}").json()


# ============================================================
# SPOOLS CRUD
# ============================================================

def test_get_spools(flask_client, spoolman_url):
    resp = flask_client.get("/api/spools")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_create_spool_bambu(flask_client, spoolman_url, cleanup_spoolman):
    resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb01"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "id" in data
    spool_id = data["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = data.get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)


def test_create_spool_inland(flask_client, spoolman_url, cleanup_spoolman):
    resp = flask_client.post(
        "/api/spools",
        json={"brand": "inland", "material": "PETG", "color_hex": "aabb02"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "id" in data
    spool_id = data["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = data.get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)


def test_create_spool_reuses_vendor(flask_client, spoolman_url, cleanup_spoolman):
    """Two spools with same brand should share one vendor."""
    before_vendors = _spoolman_get(spoolman_url, "/api/v1/vendor")
    before_count = len(before_vendors)

    for color in ("aabb03", "aabb04"):
        resp = flask_client.post(
            "/api/spools",
            json={"brand": "bambu", "material": "ABS", "color_hex": color},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        cleanup_spoolman["spools"].append(data["id"])
        fil_id = data.get("filament", {}).get("id")
        if fil_id:
            cleanup_spoolman["filaments"].append(fil_id)

    after_vendors = _spoolman_get(spoolman_url, "/api/v1/vendor")
    # Vendor count should not have increased by more than 1 (first call may create it)
    assert len(after_vendors) <= before_count + 1


def test_create_spool_reuses_filament(flask_client, spoolman_url, cleanup_spoolman):
    """Two identical spools should share one filament."""
    before_filaments = _spoolman_get(spoolman_url, "/api/v1/filament")
    before_count = len(before_filaments)

    for _ in range(2):
        resp = flask_client.post(
            "/api/spools",
            json={"brand": "bambu", "material": "TPU", "color_hex": "aabb05"},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        cleanup_spoolman["spools"].append(data["id"])
        fil_id = data.get("filament", {}).get("id")
        if fil_id and fil_id not in cleanup_spoolman["filaments"]:
            cleanup_spoolman["filaments"].append(fil_id)

    after_filaments = _spoolman_get(spoolman_url, "/api/v1/filament")
    # Filament count should not have grown by more than 1 (created on first call)
    assert len(after_filaments) <= before_count + 1


def test_update_spool(flask_client, spoolman_url, cleanup_spoolman):
    # Create a spool first
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb06"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    resp = flask_client.patch(
        f"/api/spools/{spool_id}",
        json={"remaining_weight": 500},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("remaining_weight") == 500


def test_update_spool_rejects_disallowed(flask_client, spoolman_url, cleanup_spoolman):
    """PATCH with filament_id should be silently dropped by the server."""
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb07"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    # Send disallowed field alongside an allowed one
    resp = flask_client.patch(
        f"/api/spools/{spool_id}",
        json={"remaining_weight": 750, "filament_id": 9999},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    # The remaining_weight update should have worked
    assert data.get("remaining_weight") == 750
    # The filament should not have changed to 9999
    assert data.get("filament", {}).get("id") != 9999


def test_delete_spool(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb08"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    resp = flask_client.delete(f"/api/spools/{spool_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("deleted") == spool_id


# ============================================================
# FILAMENTS CRUD
# ============================================================

def test_get_filaments(flask_client, spoolman_url):
    resp = flask_client.get("/api/filaments")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_create_filament(flask_client, spoolman_url, cleanup_spoolman):
    resp = flask_client.post(
        "/api/filaments",
        json={
            "name": "Test PLA aabb09",
            "material": "PLA",
            "color_hex": "aabb09",
            "density": 1.24,
            "diameter": 1.75,
            "weight": 1000.0,
        },
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "id" in data
    cleanup_spoolman["filaments"].append(data["id"])


def test_update_filament(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/filaments",
        json={
            "name": "Update Test aabb10",
            "material": "PETG",
            "color_hex": "aabb10",
            "density": 1.24,
            "diameter": 1.75,
            "weight": 1000.0,
        },
        content_type="application/json",
    )
    fil_id = create_resp.get_json()["id"]
    cleanup_spoolman["filaments"].append(fil_id)

    resp = flask_client.patch(
        f"/api/filaments/{fil_id}",
        json={"name": "Updated Name aabb10"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("name") == "Updated Name aabb10"


def test_delete_filament(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/filaments",
        json={
            "name": "Delete Test aabb11",
            "material": "ABS",
            "color_hex": "aabb11",
            "density": 1.05,
            "diameter": 1.75,
            "weight": 1000.0,
        },
        content_type="application/json",
    )
    fil_id = create_resp.get_json()["id"]

    resp = flask_client.delete(f"/api/filaments/{fil_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("deleted") == fil_id


# ============================================================
# VENDORS CRUD
# ============================================================

def test_get_vendors(flask_client, spoolman_url):
    resp = flask_client.get("/api/vendors")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_create_vendor(flask_client, spoolman_url, cleanup_spoolman):
    resp = flask_client.post(
        "/api/vendors",
        json={"name": "Test Vendor API aabb12"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "id" in data
    cleanup_spoolman["vendors"].append(data["id"])


def test_update_vendor(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/vendors",
        json={"name": "Vendor To Update aabb13"},
        content_type="application/json",
    )
    vendor_id = create_resp.get_json()["id"]
    cleanup_spoolman["vendors"].append(vendor_id)

    resp = flask_client.patch(
        f"/api/vendors/{vendor_id}",
        json={"name": "Vendor Updated aabb13"},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("name") == "Vendor Updated aabb13"


def test_delete_vendor(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/vendors",
        json={"name": "Vendor To Delete aabb14"},
        content_type="application/json",
    )
    vendor_id = create_resp.get_json()["id"]

    resp = flask_client.delete(f"/api/vendors/{vendor_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("deleted") == vendor_id


# ============================================================
# TRAY STATUS
# ============================================================

def test_tray_status_empty(flask_client, spoolman_url):
    resp = flask_client.get("/api/tray-status")
    assert resp.status_code == 200
    data = resp.get_json()
    for key in ("A1", "A2", "A3", "A4", "External"):
        assert key in data


def test_tray_status_with_assigned(flask_client, spoolman_url, cleanup_spoolman):
    # Create a spool
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb15"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    # Assign it to A1
    assign_resp = flask_client.post(
        f"/api/spools/{spool_id}/assign-tray",
        json={"tray": "A1"},
        content_type="application/json",
    )
    assert assign_resp.status_code == 200

    # Check tray-status
    resp = flask_client.get("/api/tray-status")
    assert resp.status_code == 200
    data = resp.get_json()
    a1 = data.get("A1")
    assert a1 is not None
    assert a1.get("spool_id") == spool_id


# ============================================================
# ASSIGN TRAY
# ============================================================

def test_assign_tray_valid(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb16"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    resp = flask_client.post(
        f"/api/spools/{spool_id}/assign-tray",
        json={"tray": "A3"},
        content_type="application/json",
    )
    assert resp.status_code == 200


def test_assign_tray_invalid(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb17"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    resp = flask_client.post(
        f"/api/spools/{spool_id}/assign-tray",
        json={"tray": "B99"},
        content_type="application/json",
    )
    assert resp.status_code == 400


def test_assign_tray_unassign(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb18"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    # Assign first
    flask_client.post(
        f"/api/spools/{spool_id}/assign-tray",
        json={"tray": "A2"},
        content_type="application/json",
    )

    # Now unassign
    resp = flask_client.post(
        f"/api/spools/{spool_id}/assign-tray",
        json={"tray": ""},
        content_type="application/json",
    )
    assert resp.status_code == 200


# ============================================================
# LINK SPOOL
# ============================================================

def test_link_spool(flask_client, spoolman_url, cleanup_spoolman):
    resp = flask_client.post(
        "/api/link-spool",
        json={
            "tag_uid": "TESTTAGAABB19",
            "type": "PLA",
            "color_hex": "aabb19",
            "tray": "A1",
        },
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "spool_id" in data
    spool_id = data["spool_id"]
    cleanup_spoolman["spools"].append(spool_id)


def test_link_spool_no_tag(flask_client, spoolman_url):
    resp = flask_client.post(
        "/api/link-spool",
        json={"type": "PLA", "color_hex": "aabb20", "tray": "A1"},
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


# ============================================================
# NFC TAG
# ============================================================

def test_get_tag(flask_client, spoolman_url, cleanup_spoolman):
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "aabb21"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    resp = flask_client.get(f"/api/tag/{spool_id}")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tag_json" in data
    tag = data["tag_json"]
    assert tag.get("protocol") == "openspool"
    assert "type" in tag
    assert "color_hex" in tag


# ============================================================
# ACTIVITY / PRINTS (use SCRIPTS_DIR override)
# ============================================================

def test_get_activity(flask_client, tmp_path):
    import server

    server.SCRIPTS_DIR = tmp_path
    records = [{"event": "swap", "ts": "2024-01-01T00:00:00"}]
    (tmp_path / "activity.json").write_text("\n".join(json.dumps(r) for r in records))

    resp = flask_client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0].get("event") == "swap"


def test_get_activity_empty(flask_client, tmp_path):
    import server

    server.SCRIPTS_DIR = tmp_path
    # Ensure no activity.json exists
    activity_file = tmp_path / "activity.json"
    if activity_file.exists():
        activity_file.unlink()

    resp = flask_client.get("/api/activity")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == []


def test_get_prints(flask_client, tmp_path):
    import server

    server.SCRIPTS_DIR = tmp_path
    records = [{"print_id": "abc123", "status": "done"}]
    (tmp_path / "prints.json").write_text("\n".join(json.dumps(r) for r in records))

    resp = flask_client.get("/api/prints")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert data[0].get("print_id") == "abc123"


# ============================================================
# PRINTER STATUS
# ============================================================

def test_printer_status_no_file(flask_client, tmp_path):
    import server

    server.SCRIPTS_DIR = tmp_path
    # Ensure bridge_state.json does not exist
    state_file = tmp_path / "bridge_state.json"
    if state_file.exists():
        state_file.unlink()

    resp = flask_client.get("/api/printer-status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("connected") is False


def test_printer_status_with_file(flask_client, tmp_path):
    import server

    server.SCRIPTS_DIR = tmp_path
    state = {
        "connected": True,
        "ams_environment": {"A1": {"material": "PLA", "color": "aabb22"}},
        "unknown_spools": [],
    }
    (tmp_path / "bridge_state.json").write_text(json.dumps(state))

    resp = flask_client.get("/api/printer-status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("connected") is True
    assert "ams_environment" in data


# ============================================================
# BRIDGE ACTIONS
# ============================================================

def test_bridge_action_invalid(flask_client, spoolman_url):
    resp = flask_client.post("/api/bridge/destroy")
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


# ============================================================
# PROXY ROUTES
# ============================================================

def test_get_locations(flask_client, spoolman_url):
    resp = flask_client.get("/api/locations")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_get_materials(flask_client, spoolman_url):
    resp = flask_client.get("/api/materials")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)


def test_export_spools(flask_client, spoolman_url):
    resp = flask_client.get("/api/export/spools?fmt=csv")
    assert resp.status_code == 200


def test_export_invalid_entity(flask_client, spoolman_url):
    resp = flask_client.get("/api/export/invalid")
    assert resp.status_code == 400
    data = resp.get_json()
    assert "error" in data


def test_spool_patch_extra(flask_client, spoolman_url, cleanup_spoolman):
    """PATCH /api/spools/<id> should accept extra field."""
    # Create a spool first
    create_resp = flask_client.post(
        "/api/spools",
        json={"brand": "bambu", "material": "PLA", "color_hex": "ff9900"},
        content_type="application/json",
    )
    spool_id = create_resp.get_json()["id"]
    cleanup_spoolman["spools"].append(spool_id)
    fil_id = create_resp.get_json().get("filament", {}).get("id")
    if fil_id:
        cleanup_spoolman["filaments"].append(fil_id)

    # Patch with extra field
    resp = flask_client.patch(
        f"/api/spools/{spool_id}",
        json={"extra": {"tag": '"TESTAG123"', "active_tray": '"A1"'}},
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["extra"]["tag"] == '"TESTAG123"'
    assert data["extra"]["active_tray"] == '"A1"'


# ============================================================
# CONFIG
# ============================================================

def test_get_config(flask_client, spoolman_url):
    resp = flask_client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "spoolman_url" in data
