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

# ============================================================
# CONFIGURATION
# ============================================================

def load_env():
    env_path = Path(__file__).parent / "bridge.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"'))

load_env()

SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")
OPENSPOOLMAN_URL = os.environ.get(
    "OPENSPOOLMAN_URL", "https://openspoolman.greenglasst.com"
)
SCRIPTS_DIR = Path(__file__).parent
PORT = int(os.environ.get("PORT", 5555))

# Material → OpenSpool type mapping
MATERIAL_MAP = {
    "pla": "PLA", "pla basic": "PLA", "pla+": "PLA", "pla matte": "PLA",
    "pla silk": "PLA", "pla-cf": "PLA-CF", "pla uv": "PLA", "pla wood": "PLA",
    "petg": "PETG", "petg basic": "PETG", "petg hf": "PETG", "petg-hf": "PETG",
    "petg-cf": "PETG-CF", "abs": "ABS", "asa": "ASA", "tpu": "TPU",
    "tpu 90a": "TPU", "pa": "PA", "pa-cf": "PA-CF", "pa6-cf": "PA-CF",
    "pc": "PC", "pva": "PVA",
}

DEFAULT_TEMPS = {
    "PLA": (190, 220), "PLA-CF": (220, 240), "PETG": (230, 260),
    "PETG-CF": (250, 270), "ABS": (240, 270), "ASA": (240, 270),
    "TPU": (210, 230), "PA": (270, 300), "PA-CF": (270, 300),
    "PC": (260, 290), "PVA": (190, 210),
}

BRANDS = {
    "bambu": {"name": "Bambu Lab", "spool_weight": 251.0, "net_weight": 1000.0},
    "inland": {"name": "Inland", "spool_weight": 145.0, "net_weight": 1000.0},
}

# ============================================================
# FLASK APP
# ============================================================

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


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
        "location": data.get("location", "Storage"),
    }
    sr = http_requests.post(f"{SPOOLMAN_URL}/api/v1/spool", json=spool_data)
    return jsonify(sr.json())


@app.route("/api/filaments", methods=["GET"])
def get_filaments():
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/filament")
    return jsonify(r.json())


@app.route("/api/vendors", methods=["GET"])
def get_vendors():
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/vendor")
    return jsonify(r.json())


# ── NFC Tag Generation ────────────────────────────────────────

@app.route("/api/tag/<int:spool_id>")
def get_tag(spool_id):
    r = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}")
    spool = r.json()
    f = spool.get("filament", {})
    vendor = f.get("vendor", {})

    material = (f.get("material") or "PLA").strip()
    color_hex = (f.get("color_hex") or "000000").lower()
    brand = vendor.get("name", "Generic")
    openspool_type = MATERIAL_MAP.get(material.lower(), material.upper())

    temps = DEFAULT_TEMPS.get(openspool_type, (190, 220))

    tag = {
        "protocol": "openspool",
        "version": "1.0",
        "type": openspool_type,
        "color_hex": color_hex.upper(),
        "brand": brand,
        "min_temp": str(temps[0]),
        "max_temp": str(temps[1]),
    }

    url = f"{OPENSPOOLMAN_URL}/spool_info?spool_id={spool_id}"

    return jsonify({"tag_json": tag, "openspool_url": url, "spool": spool})


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


@app.route("/api/config")
def get_config():
    return jsonify({
        "spoolman_url": SPOOLMAN_URL,
        "openspoolman_url": OPENSPOOLMAN_URL,
        "brands": {k: v["name"] for k, v in BRANDS.items()},
    })


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print(f"\n  SpoolBridge → http://localhost:{PORT}")
    print(f"  Spoolman API:      {SPOOLMAN_URL}")
    print(f"  OpenSpoolman:      {OPENSPOOLMAN_URL}\n")
    app.run(host="0.0.0.0", port=PORT, debug=False)
