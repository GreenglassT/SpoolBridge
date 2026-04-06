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
SCRIPTS_DIR = Path(__file__).parent
BRIDGE_COMMANDS_PATH = SCRIPTS_DIR / "bridge_commands.json"
PORT = int(os.environ.get("PORT", 5555))

BRANDS = {
    "bambu": {"name": "Bambu Lab", "spool_weight": 251.0, "net_weight": 1000.0},
    "inland": {"name": "Inland", "spool_weight": 145.0, "net_weight": 1000.0},
}

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
    brand_key = (data.get("brand") or "bambu").lower()
    brand = BRANDS.get(brand_key, BRANDS["bambu"])

    material = data.get("material", "PLA")
    color_hex = data.get("color_hex", "000000").lstrip("#")
    color_name = data.get("color_name", "")
    name = data.get("name") or f"{material} {color_name}".strip()
    price = data.get("price")

    # Find or create vendor
    vendors = http_requests.get(f"{SPOOLMAN_URL}/api/v1/vendor").json()
    vendor_id = None
    for v in vendors:
        if v["name"].lower() == brand["name"].lower():
            vendor_id = v["id"]
            break
    if not vendor_id:
        vr = http_requests.post(
            f"{SPOOLMAN_URL}/api/v1/vendor",
            json={"name": brand["name"]},
        )
        vendor_id = vr.json()["id"]

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
            "density": 1.24,
            "diameter": 1.75,
            "weight": brand["net_weight"],
            "spool_weight": brand["spool_weight"],
        }
        if price:
            fil_data["price"] = float(price)
        fr = http_requests.post(
            f"{SPOOLMAN_URL}/api/v1/filament", json=fil_data,
        )
        filament_id = fr.json()["id"]

    # Create spool
    spool_data = {
        "filament_id": filament_id,
        "remaining_weight": brand["net_weight"],
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


@app.route("/api/spools/<int:spool_id>", methods=["PATCH"])
def update_spool(spool_id):
    data = request.json or {}
    allowed = {"remaining_weight", "location", "archived"}
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

    # For AMS trays (A1-A4), push filament settings to the printer via bridge
    if tray and tray.startswith("A") and tray != "External":
        try:
            cmd = {"command": "load_ams_tray", "spool_id": spool_id, "tray": tray}
            BRIDGE_COMMANDS_PATH.write_text(json.dumps(cmd))
        except Exception as e:
            # Non-fatal — Spoolman was updated, just couldn't push to printer
            pass

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


# ── Script Runners ────────────────────────────────────────────

@app.route("/api/sync-ams", methods=["POST"])
def sync_ams():
    script = SCRIPTS_DIR / "sync-ams.sh"
    if not script.exists():
        return jsonify({"error": "sync-ams.sh not found"}), 404

    try:
        result = subprocess.run(
            ["bash", str(script)],
            capture_output=True, text=True, timeout=30, cwd=str(SCRIPTS_DIR),
        )
        return jsonify({
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Script timed out"}), 504


@app.route("/api/bridge-status")
def bridge_status():
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "openspool-bridge"],
            capture_output=True, text=True, timeout=5,
        )
        active = result.stdout.strip() == "active"
    except Exception:
        active = False

    # Fallback: check for running process
    if not active:
        try:
            result = subprocess.run(
                ["pgrep", "-f", "bridge.py"],
                capture_output=True, text=True, timeout=5,
            )
            active = result.returncode == 0
        except Exception:
            pass

    # Get recent logs
    logs = ""
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
        "brands": {k: v["name"] for k, v in BRANDS.items()},
    })


# ── Settings ──────────────────────────────────────────────────

ENV_PATH = SCRIPTS_DIR / "bridge.env"

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
