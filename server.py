#!/usr/bin/env python3
"""
SpoolBridge — Web UI for the filament tracking system.

Serves a dashboard for managing spools, writing NFC tags, syncing AMS,
and monitoring the bridge. Proxies Spoolman API and runs local scripts.

Usage:
  pip install flask requests
  python3 server.py

Runs on port 5555 by default. Set PORT env var to change.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
import requests as http_requests

from config import (
    load_env, VALID_TRAYS, TRAY_EXTERNAL, LOCATION_AMS, LOCATION_STORAGE, LOCATION_EXTERNAL,
    tray_location, build_openspool_tag, extra_quote, extra_unquote,
)

# ============================================================
# CONFIGURATION
# ============================================================

load_env()

SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")
SPOOLMAN_PUBLIC_URL = os.environ.get("SPOOLMAN_PUBLIC_URL", "")
OPENSPOOL_URL = os.environ.get("OPENSPOOL_URL", "http://openspool-f195fc.local")
SCRIPTS_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
BRIDGE_COMMANDS_PATH = SCRIPTS_DIR / "bridge_commands.json"
PORT = int(os.environ.get("PORT", 5555))

DEFAULT_SPOOL_WEIGHT = 200.0   # empty spool weight if vendor doesn't specify
DEFAULT_NET_WEIGHT = 1000.0    # filament net weight default

# ============================================================
# HELPERS
# ============================================================


def _remove_duplicate_candidate(new_tag):
    """Remove a duplicate tag candidate from bridge_state.json."""
    state_file = SCRIPTS_DIR / "bridge_state.json"
    if not state_file.exists():
        return
    try:
        state = json.loads(state_file.read_text())
        candidates = state.get("duplicate_tag_candidates", [])
        state["duplicate_tag_candidates"] = [
            c for c in candidates if c.get("new_tag") != new_tag
        ]
        state_file.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.after_request
def no_cache_html(response):
    if response.content_type and "text/html" in response.content_type:
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers.pop("ETag", None)
        response.headers.pop("Last-Modified", None)
    return response


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/spool/<int:spool_id>")
def spool_info(spool_id):
    return send_from_directory("static", "spool.html")


# ── Spoolman Proxy ────────────────────────────────────────────

@app.route("/api/spools", methods=["GET"])
def get_spools():
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool")
    return jsonify(r.json())


@app.route("/api/spools", methods=["POST"])
def create_spool():
    data = request.json
    brand_name = data.get("brand", "").strip()

    material = data.get("material", "PLA")
    color_hex = data.get("color_hex", "000000").lstrip("#")
    color_name = data.get("color_name", "")
    name = data.get("name") or f"{material} {color_name}".strip()
    price = data.get("price")

    # Find or create vendor
    vendors = http_requests.get(f"{SPOOLMAN_URL}/api/v1/vendor").json()
    vendor = None
    for v in vendors:
        if v["name"].lower() == brand_name.lower():
            vendor = v
            break
    if not vendor:
        new_vendor_weight = data.get("spool_weight") or DEFAULT_SPOOL_WEIGHT
        vr = http_requests.post(
            f"{SPOOLMAN_URL}/api/v1/vendor",
            json={"name": brand_name, "empty_spool_weight": new_vendor_weight},
        )
        vendor = vr.json()

    vendor_id = vendor["id"]
    spool_weight = data.get("spool_weight") or vendor.get("empty_spool_weight") or DEFAULT_SPOOL_WEIGHT
    net_weight = data.get("net_weight") or DEFAULT_NET_WEIGHT
    diameter = data.get("diameter") or 1.75
    density = data.get("density") or 1.24
    nozzle_temp = data.get("nozzle_temp")
    bed_temp = data.get("bed_temp")

    # Check for existing filament
    filaments = http_requests.get(f"{SPOOLMAN_URL}/api/v1/filament").json()
    filament_id = None
    for f in filaments:
        f_mat = (f.get("material") or "").lower()
        f_color = (f.get("color_hex") or "").lower()
        f_vendor = (f.get("vendor") or {}).get("id")
        if f_mat == material.lower() and f_color == color_hex.lower() and f_vendor == vendor_id:
            filament_id = f["id"]
            break

    if not filament_id:
        fil_data = {
            "name": name,
            "vendor_id": vendor_id,
            "material": material,
            "color_hex": color_hex,
            "density": density,
            "diameter": diameter,
            "weight": net_weight,
            "spool_weight": spool_weight,
        }
        if price:
            fil_data["price"] = float(price)
        if nozzle_temp:
            fil_data["settings_extruder_temp"] = int(nozzle_temp)
        if bed_temp:
            fil_data["settings_bed_temp"] = int(bed_temp)
        fr = http_requests.post(
            f"{SPOOLMAN_URL}/api/v1/filament", json=fil_data,
        )
        filament_id = fr.json()["id"]

    # Create spool
    spool_data = {
        "filament_id": filament_id,
        "remaining_weight": net_weight,
        "location": data.get("location", LOCATION_STORAGE),
    }
    sr = http_requests.post(f"{SPOOLMAN_URL}/api/v1/spool", json=spool_data)
    return jsonify(sr.json())


@app.route("/api/spools/from-filament/<int:filament_id>", methods=["POST"])
def create_spool_from_filament(filament_id):
    """Create a new spool from an existing filament."""
    try:
        r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/filament/{filament_id}")
        r.raise_for_status()
        fil = r.json()
    except http_requests.exceptions.RequestException:
        return jsonify({"error": "Filament not found"}), 404

    weight = fil.get("weight") or 1000.0
    spool_data = {
        "filament_id": filament_id,
        "remaining_weight": weight,
        "location": LOCATION_STORAGE,
    }
    sr = http_requests.post(f"{SPOOLMAN_URL}/api/v1/spool", json=spool_data)
    return jsonify(sr.json())


@app.route("/api/filaments", methods=["GET"])
def get_filaments():
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/filament")
    return jsonify(r.json())


@app.route("/api/filaments", methods=["POST"])
def create_filament():
    data = request.json or {}
    try:
        r = http_requests.post(f"{SPOOLMAN_URL}/api/v1/filament", json=data)
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/filaments/<int:filament_id>", methods=["PATCH"])
def update_filament(filament_id):
    data = request.json or {}
    try:
        r = http_requests.patch(f"{SPOOLMAN_URL}/api/v1/filament/{filament_id}", json=data)
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/filaments/<int:filament_id>", methods=["DELETE"])
def delete_filament(filament_id):
    try:
        r = http_requests.delete(f"{SPOOLMAN_URL}/api/v1/filament/{filament_id}")
        r.raise_for_status()
        return jsonify({"deleted": filament_id})
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/vendors", methods=["GET"])
def get_vendors():
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/vendor")
    return jsonify(r.json())


@app.route("/api/vendors", methods=["POST"])
def create_vendor():
    data = request.json or {}
    try:
        r = http_requests.post(f"{SPOOLMAN_URL}/api/v1/vendor", json=data)
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/vendors/<int:vendor_id>", methods=["PATCH"])
def update_vendor(vendor_id):
    data = request.json or {}
    try:
        r = http_requests.patch(f"{SPOOLMAN_URL}/api/v1/vendor/{vendor_id}", json=data)
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/vendors/<int:vendor_id>", methods=["DELETE"])
def delete_vendor(vendor_id):
    try:
        r = http_requests.delete(f"{SPOOLMAN_URL}/api/v1/vendor/{vendor_id}")
        r.raise_for_status()
        return jsonify({"deleted": vendor_id})
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/tray-status", methods=["GET"])
def tray_status():
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool")
    spools = r.json()

    trays = {t: None for t in VALID_TRAYS}

    for s in spools:
        extra = s.get("extra") or {}
        active_tray = extra.get("active_tray")
        if not active_tray:
            continue
        tray_name = extra_unquote(active_tray)
        if not tray_name:
            continue

        # Normalize old-format keys like "22E8AJ5C1000237_0_1" → "A2"
        if "_" in tray_name and tray_name not in VALID_TRAYS:
            parts = tray_name.rsplit("_", 1)
            try:
                tray_name = f"A{int(parts[-1]) + 1}"
            except (ValueError, IndexError):
                continue

        if tray_name not in trays:
            continue

        fil = s.get("filament") or {}
        vendor = fil.get("vendor") or {}
        trays[tray_name] = {
            "spool_id": s.get("id"),
            "name": fil.get("name"),
            "material": fil.get("material"),
            "color_hex": fil.get("color_hex"),
            "brand": vendor.get("name"),
            "remaining_weight": s.get("remaining_weight"),
            "total_weight": fil.get("weight"),
        }

    return jsonify(trays)


def _read_jsonl(path, limit=None):
    if not path.exists():
        return []
    events = [json.loads(l) for l in path.read_text().strip().splitlines() if l.strip()]
    events.reverse()
    return events[:limit] if limit else events


@app.route("/api/activity", methods=["GET"])
def get_activity():
    return jsonify(_read_jsonl(SCRIPTS_DIR / "activity.json", limit=50))


@app.route("/api/activity", methods=["DELETE"])
def clear_activity():
    path = SCRIPTS_DIR / "activity.json"
    try:
        path.write_text("")
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prints", methods=["GET"])
def get_prints():
    return jsonify(_read_jsonl(SCRIPTS_DIR / "prints.json"))


@app.route("/api/usage", methods=["GET"])
def get_usage():
    return jsonify(_read_jsonl(SCRIPTS_DIR / "usage.json", limit=500))


@app.route("/api/spools/<int:spool_id>", methods=["PATCH"])
def update_spool(spool_id):
    data = request.json or {}
    allowed = {"remaining_weight", "location", "archived", "extra"}
    payload = {k: v for k, v in data.items() if k in allowed}

    try:
        r = http_requests.patch(
            f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", json=payload,
        )
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/spools/<int:spool_id>", methods=["DELETE"])
def delete_spool(spool_id):
    try:
        r = http_requests.delete(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}")
        r.raise_for_status()
        return jsonify({"deleted": spool_id})
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/printer-status")
def printer_status():
    state_file = SCRIPTS_DIR / "bridge_state.json"
    if not state_file.exists():
        return jsonify({"connected": False, "ams_environment": {}, "unknown_spools": []})
    try:
        state = json.loads(state_file.read_text())
        return jsonify(state)
    except Exception:
        return jsonify({"connected": False, "ams_environment": {}, "unknown_spools": []})


@app.route("/api/link-spool", methods=["POST"])
def link_spool():
    data = request.json or {}
    tag_uid = data.get("tag_uid", "")
    tray_type = data.get("type", "PLA")
    color_hex = data.get("color_hex", "000000").lstrip("#")[:6]
    name = data.get("name", f"{tray_type} {color_hex}")
    tray = data.get("tray", "")

    if not tag_uid:
        return jsonify({"error": "tag_uid is required"}), 400

    # Check if a spool with this tag already exists
    existing = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool").json()
    for ex in existing:
        ex_tag = extra_unquote((ex.get("extra") or {}).get("tag", ""))
        if ex_tag == tag_uid:
            return jsonify({"spool_id": ex["id"], "linked_tag": tag_uid, "existing": True})

    # Find or create filament
    filaments = http_requests.get(f"{SPOOLMAN_URL}/api/v1/filament").json()
    filament_id = None
    for f in filaments:
        f_mat = (f.get("material") or "").lower()
        f_color = (f.get("color_hex") or "").lower()
        if f_mat == tray_type.lower() and f_color == color_hex.lower():
            filament_id = f["id"]
            break

    if not filament_id:
        fil_r = http_requests.post(f"{SPOOLMAN_URL}/api/v1/filament", json={
            "name": name,
            "material": tray_type,
            "color_hex": color_hex,
            "density": 1.24,
            "diameter": 1.75,
            "weight": 1000.0,
            "spool_weight": 251.0,
            "vendor_id": 1,
        })
        filament_id = fil_r.json().get("id")

    spool_r = http_requests.post(f"{SPOOLMAN_URL}/api/v1/spool", json={
        "filament_id": filament_id,
        "remaining_weight": 1000,
        "location": LOCATION_AMS,
    })
    spool = spool_r.json()
    spool_id = spool.get("id")

    if not spool_id:
        return jsonify({"error": "Failed to create spool"}), 500

    http_requests.patch(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", json={
        "extra": {
            "tag": extra_quote(tag_uid),
            "active_tray": extra_quote(tray),
        },
    })

    return jsonify({"spool_id": spool_id, "linked_tag": tag_uid})


@app.route("/api/resolve-duplicate-tag", methods=["POST"])
def resolve_duplicate_tag():
    """Link a second RFID tag to an existing spool (same spool, other side)."""
    data = request.json or {}
    spool_id = data.get("spool_id")
    new_tag = data.get("new_tag_uid", "")
    tray = data.get("tray", "")

    if not spool_id or not new_tag:
        return jsonify({"error": "spool_id and new_tag_uid are required"}), 400

    try:
        http_requests.patch(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", json={
            "extra": {
                "tag2": extra_quote(new_tag),
                "active_tray": extra_quote(tray),
            },
            "location": tray_location(tray),
        })
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502

    _remove_duplicate_candidate(new_tag)

    return jsonify({"spool_id": spool_id, "tag2": new_tag, "tray": tray})


@app.route("/api/reject-duplicate-tag", methods=["POST"])
def reject_duplicate_tag():
    """User confirmed this is a new spool, not a duplicate. Create it via link-spool."""
    data = request.json or {}
    new_tag = data.get("new_tag_uid", "")

    _remove_duplicate_candidate(new_tag)

    # Delegate to link-spool to create the new spool
    return link_spool()


@app.route("/api/spools/<int:spool_id>/assign-tray", methods=["POST"])
@app.route("/api/spools/<int:spool_id>/assign-tray/<tray_name>", methods=["POST"])
def assign_tray(spool_id, tray_name=None):
    if tray_name is not None:
        tray = tray_name
    else:
        data = request.get_json(silent=True) or {}
        tray = data.get("tray", "")
    if tray and tray not in VALID_TRAYS:
        return jsonify({"error": f"Invalid tray. Use {', '.join(VALID_TRAYS)}, or empty to unassign."}), 400

    location = tray_location(tray)
    payload = {
        "extra": {"active_tray": extra_quote(tray)},
        "location": location,
    }

    try:
        r = http_requests.patch(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", json=payload)
        r.raise_for_status()
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502

    return jsonify(r.json())


@app.route("/api/load-external/<int:spool_id>", methods=["POST"])
def load_external(spool_id):
    """Configure printer's external tray with this spool via MQTT."""
    try:
        r = http_requests.patch(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}", json={
            "extra": {"active_tray": extra_quote(TRAY_EXTERNAL)},
            "location": LOCATION_EXTERNAL,
        })
        r.raise_for_status()
    except http_requests.exceptions.RequestException:
        return jsonify({"error": "Spool not found"}), 404

    # Write command for bridge to publish MQTT to the printer
    cmd = {"command": "load_external", "spool_id": spool_id}
    try:
        BRIDGE_COMMANDS_PATH.write_text(json.dumps(cmd))
    except Exception as e:
        return jsonify({"error": f"Failed to write command: {e}"}), 500

    return jsonify({"status": "ok", "message": f"Loading spool #{spool_id} to external tray"})


# ── NFC Tag Generation ────────────────────────────────────────

@app.route("/api/tag/<int:spool_id>")
def get_tag(spool_id):
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}")
    spool = r.json()
    tag = build_openspool_tag(spool)
    spool_url = f"{request.host_url}spool/{spool_id}"
    return jsonify({"tag_json": tag, "spool_url": spool_url, "spool": spool})


# ── OpenSpool NFC Write ───────────────────────────────────────

@app.route("/api/openspool/write/<int:spool_id>", methods=["POST"])
def openspool_write_tag(spool_id):
    """Send filament data to OpenSpool device and trigger NFC tag write.

    Sets filament params on the ESP32 via ESPHome REST API (entity-name
    URLs with spaces), then presses Write NFC.  The device enters write
    mode for 30 seconds, then auto-cancels back to read mode.
    """
    try:
        r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}")
        r.raise_for_status()
        spool = r.json()
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch spool: {e}"}), 502

    tag = build_openspool_tag(spool)
    public = SPOOLMAN_PUBLIC_URL or request.host_url.rstrip("/")

    try:
        session = http_requests.Session()
        for endpoint, params in [
            ("select/Filament Type/set", {"option": tag["type"]}),
            ("select/Filament Brand/set", {"option": tag["brand"]}),
            ("text/Custom Color Hex/set", {"value": tag["color_hex"]}),
            ("number/Filament Min Temp/set", {"value": tag["min_temp"]}),
            ("number/Filament Max Temp/set", {"value": tag["max_temp"]}),
            ("text/Spool URL/set", {"value": f"{public}/spool/{spool_id}"}),
        ]:
            resp = session.post(f"{OPENSPOOL_URL}/{endpoint}", params=params, timeout=2)
            resp.raise_for_status()
        # Trigger write after all fields are set
        session.post(f"{OPENSPOOL_URL}/button/Write NFC/press", timeout=2).raise_for_status()
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to reach OpenSpool device: {e}"}), 502

    return jsonify({
        "status": "writing",
        "message": "OpenSpool is waiting for tag (30s timeout). Tap an NTAG215 on the reader.",
        "tag_data": tag,
    })


# ── Spoolman Proxy Routes ────────────────────────────────────

@app.route("/api/spools/<int:spool_id>/use", methods=["PUT"])
def use_spool(spool_id):
    """Deduct filament from spool by length used (mm)."""
    try:
        r = http_requests.put(
            f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}/use",
            json=request.json or {},
        )
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/spools/<int:spool_id>/measure", methods=["PUT"])
def measure_spool(spool_id):
    """Update spool weight from a scale measurement."""
    try:
        r = http_requests.put(
            f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}/measure",
            json=request.json or {},
        )
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/locations", methods=["GET"])
def get_locations():
    """List all spool locations from Spoolman."""
    try:
        r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/location")
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/materials", methods=["GET"])
def get_materials():
    """List all materials from Spoolman."""
    try:
        r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/material")
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/backup", methods=["POST"])
def create_backup():
    """Trigger a Spoolman database backup."""
    try:
        r = http_requests.post(f"{SPOOLMAN_URL}/api/v1/backup")
        r.raise_for_status()
        return jsonify(r.json())
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/export/<entity>", methods=["GET"])
def export_data(entity):
    """Export spools, filaments, or vendors from Spoolman."""
    allowed_entities = {"spools", "filaments", "vendors"}
    if entity not in allowed_entities:
        return jsonify({"error": f"Invalid entity: {entity}"}), 400
    try:
        r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/export/{entity}", params=request.args)
        r.raise_for_status()
        return (r.content, r.status_code, {"Content-Type": r.headers.get("Content-Type", "application/json")})
    except http_requests.exceptions.RequestException as e:
        return jsonify({"error": str(e)}), 502


# ── Script Runners ────────────────────────────────────────────

@app.route("/api/sync-ams", methods=["POST"])
def sync_ams():
    app_dir = Path(__file__).parent
    script = app_dir / "sync-ams.sh"
    if not script.exists():
        return jsonify({"error": "sync-ams.sh not found"}), 404

    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=30, cwd=str(app_dir),
        )
        return jsonify({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Script timed out"}), 504


BRIDGE_CONTAINER = "spoolbridge-bridge"


@app.route("/api/bridge-status")
def bridge_status():
    active = False
    logs = ""

    # Try Docker first
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", BRIDGE_CONTAINER],
            capture_output=True, text=True, timeout=5,
        )
        active = result.stdout.strip() == "true"
    except Exception:
        pass

    # Fallback: systemd
    if not active:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "openspool-bridge"],
                capture_output=True, text=True, timeout=5,
            )
            active = result.stdout.strip() == "active"
        except Exception:
            pass

    # Fallback: process check
    if not active:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "bridge.py"],
                capture_output=True, text=True, timeout=5,
            )
            active = result.returncode == 0
        except Exception:
            pass

    # Get logs — try Docker first, then journalctl
    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "30", BRIDGE_CONTAINER],
            capture_output=True, text=True, timeout=5,
        )
        logs = result.stdout or result.stderr
    except Exception:
        try:
            result = subprocess.run(
                ["journalctl", "-u", "openspool-bridge", "-n", "20", "--no-pager"],
                capture_output=True, text=True, timeout=5,
            )
            logs = result.stdout
        except Exception:
            pass

    return jsonify({"active": active, "logs": logs})


ALLOWED_BRIDGE_ACTIONS = {"start", "stop", "restart"}


@app.route("/api/bridge/<action>", methods=["POST"])
def bridge_action(action):
    if action not in ALLOWED_BRIDGE_ACTIONS:
        return jsonify({"error": "Invalid action"}), 400

    # Try Docker first
    try:
        result = subprocess.run(
            ["docker", action, BRIDGE_CONTAINER],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0:
            return jsonify({"status": "ok"})
    except Exception:
        pass

    # Fallback: systemd
    try:
        result = subprocess.run(
            ["systemctl", action, "openspool-bridge"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return jsonify({"status": "ok"})
        return jsonify({"error": result.stderr.strip() or f"Failed to {action} service"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/config")
def get_config():
    # Build a browser-accessible Spoolman URL
    if SPOOLMAN_PUBLIC_URL:
        public_url = SPOOLMAN_PUBLIC_URL
    else:
        # Same host as the request, port 7912
        host = request.host.split(":")[0]
        scheme = request.scheme
        public_url = f"{scheme}://{host}:7912"

    return jsonify({
        "spoolman_url": public_url,
    })


# ── Settings ──────────────────────────────────────────────────

ENV_PATH = Path(__file__).parent / "bridge.env"

# Keys we allow reading/writing, in display order
SETTINGS_KEYS = [
    "PRINTER_IP",
    "PRINTER_SERIAL",
    "PRINTER_ACCESS_CODE",
    "SPOOLMAN_URL",
    "SPOOLMAN_PUBLIC_URL",
]


def _parse_env_file():
    """Parse bridge.env into {key: value} dict."""
    result = {}
    if not ENV_PATH.exists():
        return result
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            result[k.strip()] = v.strip()
    return result


def _mask_secret(value, visible=4):
    """Mask a secret, showing only the first `visible` characters."""
    if not value or len(value) <= visible:
        return value
    return value[:visible] + "••••"


@app.route("/api/settings", methods=["GET"])
def get_settings():
    env = _parse_env_file()
    masked = {}
    for key in SETTINGS_KEYS:
        val = env.get(key, "")
        if key == "PRINTER_ACCESS_CODE":
            val = _mask_secret(val)
        masked[key] = val
    return jsonify(masked)


@app.route("/api/settings", methods=["POST"])
def save_settings():
    data = request.json or {}

    # Validate
    ip = data.get("PRINTER_IP", "").strip()
    if ip:
        import re
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip):
            return jsonify({"error": "Invalid IP address format"}), 400

    serial = data.get("PRINTER_SERIAL", "").strip()
    if serial and not serial.isalnum():
        return jsonify({"error": "Serial must be alphanumeric"}), 400

    # Read existing env to preserve comments and unmanaged keys
    existing = _parse_env_file()

    # Update only the keys we manage
    for key in SETTINGS_KEYS:
        if key not in data:
            continue
        new_val = data[key].strip()
        # Don't overwrite access code with the masked value
        if key == "PRINTER_ACCESS_CODE" and "••" in new_val:
            continue
        existing[key] = new_val

    # Rebuild the env file preserving comments
    lines = []
    if ENV_PATH.exists():
        written_keys = set()
        for line in ENV_PATH.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in existing:
                    lines.append(f"{k}={existing[k]}")
                    written_keys.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
        # Append any new keys not in the original file
        for k in SETTINGS_KEYS:
            if k in existing and k not in written_keys:
                lines.append(f"{k}={existing[k]}")
    else:
        lines.append("# SpoolBridge Configuration")
        lines.append("")
        for k in SETTINGS_KEYS:
            lines.append(f"{k}={existing.get(k, '')}")

    ENV_PATH.write_text("\n".join(lines) + "\n")
    return jsonify({"status": "ok", "message": "Settings saved. Restart services to apply."})


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print(f"\n  SpoolBridge → http://localhost:{PORT}")
    print(f"  Spoolman API:      {SPOOLMAN_URL}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
