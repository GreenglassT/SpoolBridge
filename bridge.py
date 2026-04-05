#!/usr/bin/env python3
"""
SpoolBridge — MQTT Sync Daemon

Runs as a daemon on the Debian server. Subscribes to the P2S printer's MQTT
broker and syncs filament changes to Spoolman:

1. External spool changes (from OpenSpool Mini NFC taps)
2. AMS tray changes (from loading Bambu spools with RFID)
3. Print completion (updates remaining weight in Spoolman)

Usage:
  ./bridge.py                          # run with .env config
  PRINTER_IP=x.x.x.x ./bridge.py      # override via env

Requires: pip install paho-mqtt requests
"""

import json
import os
import ssl
import sys
import time
import logging
from pathlib import Path

import paho.mqtt.client as mqtt
import requests

# ============================================================
# CONFIGURATION — override via environment or .env file
# ============================================================

def load_env():
    """Load .env file from script directory if it exists."""
    env_path = Path(__file__).parent / "bridge.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"'))

load_env()

PRINTER_IP = os.environ.get("PRINTER_IP", "")
PRINTER_SERIAL = os.environ.get("PRINTER_SERIAL", "")
PRINTER_ACCESS_CODE = os.environ.get("PRINTER_ACCESS_CODE", "")
SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")

MQTT_PORT = 8883
MQTT_USER = "bblp"

# How often to push a full status request (seconds)
POLL_INTERVAL = 30

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bridge")

# ============================================================
# STATE TRACKING
# ============================================================

# Track what's currently loaded so we only sync on changes
current_vt_tray = {}       # external spool (virtual tray)
current_ams_trays = {}     # {slot_id: {type, color, tag_uid, remain}}
active_spool_id = None     # Spoolman spool ID for external slot


# ============================================================
# SPOOLMAN API HELPERS
# ============================================================

def sm_get(endpoint):
    """GET from Spoolman API."""
    r = requests.get(f"{SPOOLMAN_URL}/api/v1/{endpoint}")
    r.raise_for_status()
    return r.json()


def sm_patch(endpoint, data):
    """PATCH to Spoolman API."""
    r = requests.patch(
        f"{SPOOLMAN_URL}/api/v1/{endpoint}",
        json=data,
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    return r.json()


def find_spool_by_color_material(color_hex, material):
    """Find a Spoolman spool matching color + material type."""
    color_hex = color_hex.lower().rstrip("ff")[:6]
    material = material.strip()

    spools = sm_get("spool")
    candidates = []

    for s in spools:
        f = s.get("filament", {})
        f_color = (f.get("color_hex") or "").lower()
        f_material = (f.get("material") or "").strip()

        # Normalize material names: "PLA Basic" -> also match "PLA"
        mat_match = (
            f_material.lower() == material.lower()
            or f_material.lower().startswith(material.lower())
            or material.lower().startswith(f_material.lower())
        )

        if f_color == color_hex and mat_match:
            candidates.append(s)

    if len(candidates) == 1:
        return candidates[0]
    elif len(candidates) > 1:
        log.warning(
            f"Multiple spools match {material} #{color_hex}: "
            f"{[s['id'] for s in candidates]}. Using first."
        )
        return candidates[0]
    return None


def find_spool_by_tag(tag_uid):
    """Find a Spoolman spool by its RFID tag UID."""
    if not tag_uid:
        return None
    spools = sm_get("spool")
    for s in spools:
        extra = s.get("extra", {})
        tag = extra.get("tag", "").strip('"')
        if tag and tag == tag_uid:
            return s
    return None


def update_spool_tray(spool_id, tray_name):
    """Set which tray a spool is in."""
    try:
        sm_patch(f"spool/{spool_id}", {
            "extra": {"active_tray": f'"{tray_name}"'},
            "location": "AMS 2 Pro" if tray_name.startswith("A") else "External",
        })
        log.info(f"Spool #{spool_id} → {tray_name}")
    except Exception as e:
        log.error(f"Failed to update spool #{spool_id}: {e}")


def update_spool_weight(spool_id, used_grams):
    """Subtract used filament from a spool's remaining weight."""
    try:
        spool = sm_get(f"spool/{spool_id}")
        current = spool.get("remaining_weight", 1000)
        new_weight = max(0, current - used_grams)
        sm_patch(f"spool/{spool_id}", {"remaining_weight": new_weight})
        log.info(f"Spool #{spool_id}: {current:.0f}g → {new_weight:.0f}g (-{used_grams:.1f}g)")
    except Exception as e:
        log.error(f"Failed to update weight for spool #{spool_id}: {e}")


# ============================================================
# EVENT LOGGING
# ============================================================

SCRIPT_DIR = Path(__file__).parent


def log_event(event_type, tray="", spool_id=None, spool_name="", detail=""):
    """Append an event to activity.json (JSON-lines, capped at 200 entries)."""
    try:
        path = SCRIPT_DIR / "activity.json"
        entry = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "type": event_type,
            "tray": tray,
            "spool_id": spool_id,
            "spool_name": spool_name,
            "detail": detail,
        })
        # Read existing lines, append new, cap at 200
        lines = []
        if path.exists():
            lines = path.read_text().strip().splitlines()
        lines.append(entry)
        if len(lines) > 200:
            lines = lines[-200:]
        path.write_text("\n".join(lines) + "\n")
    except Exception as e:
        log.error(f"Failed to write activity.json: {e}")


def log_print(spool_id, spool_name, material, color_hex, grams_used, status):
    """Append a print record to prints.json (JSON-lines, append-only)."""
    try:
        path = SCRIPT_DIR / "prints.json"
        entry = json.dumps({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "spool_id": spool_id,
            "spool_name": spool_name,
            "material": material,
            "color_hex": color_hex,
            "grams_used": grams_used,
            "status": status,
        })
        with open(path, "a") as f:
            f.write(entry + "\n")
    except Exception as e:
        log.error(f"Failed to write prints.json: {e}")


# ============================================================
# MQTT MESSAGE PARSING
# ============================================================

def parse_vt_tray(msg_data):
    """Parse external/virtual tray data from printer MQTT report."""
    vt = msg_data.get("print", {}).get("vt_tray", {})
    if not vt:
        return None

    tray_type = vt.get("tray_type", "")
    tray_color = vt.get("tray_color", "")

    if not tray_type:
        return None

    return {
        "type": tray_type,
        "color": tray_color[:6] if tray_color else "",
        "nozzle_min": vt.get("nozzle_temp_min", 0),
        "nozzle_max": vt.get("nozzle_temp_max", 0),
    }


def parse_ams_trays(msg_data):
    """Parse AMS tray data from printer MQTT report."""
    ams_list = msg_data.get("print", {}).get("ams", {}).get("ams", [])
    trays = {}

    for ams_unit in ams_list:
        ams_id = ams_unit.get("id", "0")
        for tray in ams_unit.get("tray", []):
            tray_id = tray.get("id", "0")
            slot_key = f"A{ams_id}_{tray_id}"
            tray_type = tray.get("tray_type", "")

            if tray_type:
                trays[slot_key] = {
                    "type": tray_type,
                    "color": (tray.get("tray_color") or "")[:6],
                    "tag_uid": tray.get("tag_uid", ""),
                    "remain": tray.get("remain", -1),
                }

    return trays


def parse_print_finish(msg_data):
    """Check if a print just finished and get filament usage."""
    mc_print = msg_data.get("print", {})

    # Print state transitions
    gcode_state = mc_print.get("gcode_state", "")
    if gcode_state != "FINISH" and gcode_state != "FAILED":
        return None

    # Try to get filament usage from the print stats
    # Bambu reports total filament used in the print job
    total_used = mc_print.get("total_weight", 0)  # grams
    status = gcode_state.lower()  # "finish" or "failed"
    if total_used and total_used > 0:
        return {"used_grams": total_used, "status": status}

    return {"used_grams": 0, "status": status}


# ============================================================
# SYNC LOGIC
# ============================================================

def handle_vt_tray_change(new_vt):
    """Handle external spool change (OpenSpool Mini tap)."""
    global current_vt_tray, active_spool_id

    if new_vt == current_vt_tray:
        return

    old_vt = current_vt_tray
    current_vt_tray = new_vt

    log.info(
        f"External spool changed: {new_vt['type']} #{new_vt['color']} "
        f"({new_vt['nozzle_min']}-{new_vt['nozzle_max']}°C)"
    )

    # Try to find matching spool in Spoolman
    spool = find_spool_by_color_material(new_vt["color"], new_vt["type"])

    if spool:
        active_spool_id = spool["id"]
        f = spool.get("filament", {})
        log.info(
            f"Matched → Spool #{spool['id']}: "
            f"{f.get('name', '?')} ({f.get('material', '?')})"
        )
        update_spool_tray(spool["id"], "External")
        log_event("external_tap", tray="External", spool_id=spool["id"], spool_name=f.get("name", "?"), detail=f"{f.get('material', '?')} #{new_vt['color']}")
    else:
        active_spool_id = None
        log.warning(
            f"No Spoolman spool matches {new_vt['type']} #{new_vt['color']}. "
            f"Create it in Spoolman first."
        )
        log_event("external_tap", tray="External", detail=f"Unmatched: {new_vt['type']} #{new_vt['color']}")


def handle_ams_change(new_trays):
    """Handle AMS tray changes (Bambu RFID detection)."""
    global current_ams_trays

    for slot_key, tray_data in new_trays.items():
        old_data = current_ams_trays.get(slot_key)
        if old_data == tray_data:
            continue

        tag_uid = tray_data.get("tag_uid", "")
        tray_num = int(slot_key.split("_")[1]) + 1

        if tag_uid:
            spool = find_spool_by_tag(tag_uid)
            if spool:
                f = spool.get("filament", {})
                log.info(
                    f"AMS Tray {tray_num}: {f.get('name', '?')} "
                    f"(spool #{spool['id']}, tag {tag_uid[:8]}...)"
                )
                update_spool_tray(spool["id"], f"A{tray_num}")
                log_event("tray_load", tray=f"A{tray_num}", spool_id=spool["id"], spool_name=f.get("name", "?"), detail=f"tag {tag_uid[:8]}...")
            else:
                log.info(
                    f"AMS Tray {tray_num}: Unknown tag {tag_uid[:8]}... "
                    f"({tray_data['type']} #{tray_data['color']})"
                )
        else:
            # Third-party spool in AMS, no RFID — try color+material match
            spool = find_spool_by_color_material(tray_data["color"], tray_data["type"])
            if spool:
                f = spool.get("filament", {})
                log.info(
                    f"AMS Tray {tray_num}: Matched by color → "
                    f"{f.get('name', '?')} (spool #{spool['id']})"
                )
                update_spool_tray(spool["id"], f"A{tray_num}")
                log_event("tray_load", tray=f"A{tray_num}", spool_id=spool["id"], spool_name=f.get("name", "?"), detail="color match")

    current_ams_trays = new_trays


def handle_print_finish(finish_data):
    """Handle print completion — update filament weight and log events."""
    global active_spool_id

    used = finish_data.get("used_grams", 0)
    status = finish_data.get("status", "finish")

    # Get spool info before updating weight
    spool_name = ""
    material = ""
    color_hex = ""
    if active_spool_id:
        try:
            spool = sm_get(f"spool/{active_spool_id}")
            f = spool.get("filament", {})
            spool_name = f.get("name", "?")
            material = f.get("material", "?")
            color_hex = f.get("color_hex", "")
        except Exception:
            pass

    if used > 0 and active_spool_id:
        log.info(f"Print {status}, used {used:.1f}g from spool #{active_spool_id}")
        update_spool_weight(active_spool_id, used)
    elif used > 0:
        log.info(f"Print {status}, used {used:.1f}g but no active spool tracked")
    else:
        log.info(f"Print {status}, no filament usage reported")

    log_event(
        "print_done",
        spool_id=active_spool_id,
        spool_name=spool_name,
        detail=f"{status}, {used:.1f}g used",
    )
    log_print(
        spool_id=active_spool_id,
        spool_name=spool_name,
        material=material,
        color_hex=color_hex,
        grams_used=used,
        status=status,
    )


# ============================================================
# MQTT CALLBACKS
# ============================================================

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        topic = f"device/{PRINTER_SERIAL}/report"
        client.subscribe(topic)
        log.info(f"Connected to P2S at {PRINTER_IP}")
        log.info(f"Subscribed to {topic}")

        # Request full status dump
        request_topic = f"device/{PRINTER_SERIAL}/request"
        client.publish(request_topic, json.dumps({"pushing": {"command": "pushall"}}))
    else:
        log.error(f"MQTT connect failed: rc={rc}")


def on_disconnect(client, userdata, rc, properties=None, reason_code=None):
    log.warning(f"Disconnected from MQTT (rc={rc}), reconnecting...")


def on_message(client, userdata, msg):
    try:
        data = json.loads(msg.payload)
    except json.JSONDecodeError:
        return

    # Parse external spool (virtual tray)
    vt = parse_vt_tray(data)
    if vt:
        handle_vt_tray_change(vt)

    # Parse AMS trays
    ams = parse_ams_trays(data)
    if ams:
        handle_ams_change(ams)

    # Check for print completion
    finish = parse_print_finish(data)
    if finish:
        handle_print_finish(finish)


# ============================================================
# MAIN
# ============================================================

def main():
    if not all([PRINTER_IP, PRINTER_SERIAL, PRINTER_ACCESS_CODE]):
        log.error(
            "Missing config. Set PRINTER_IP, PRINTER_SERIAL, PRINTER_ACCESS_CODE\n"
            "  via environment variables or bridge.env file."
        )
        sys.exit(1)

    log.info("=== SpoolBridge — MQTT Sync Daemon ===")
    log.info(f"Printer:  {PRINTER_IP} (serial: {PRINTER_SERIAL})")
    log.info(f"Spoolman: {SPOOLMAN_URL}")

    # Verify Spoolman is reachable
    try:
        info = requests.get(f"{SPOOLMAN_URL}/api/v1/info", timeout=5)
        log.info(f"Spoolman OK: v{info.json().get('version', '?')}")
    except Exception as e:
        log.error(f"Cannot reach Spoolman at {SPOOLMAN_URL}: {e}")
        sys.exit(1)

    # Set up MQTT client
    client = mqtt.Client(
        client_id=f"openspool-bridge-{int(time.time())}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(MQTT_USER, PRINTER_ACCESS_CODE)

    # TLS without cert verification (printer uses self-signed cert)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    client.tls_set_context(ctx)

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message

    # Connect and loop
    try:
        client.connect(PRINTER_IP, MQTT_PORT, keepalive=60)
    except Exception as e:
        log.error(f"Cannot connect to printer MQTT: {e}")
        sys.exit(1)

    # Run forever, periodically requesting status updates
    client.loop_start()

    try:
        while True:
            time.sleep(POLL_INTERVAL)
            # Periodic pushall to stay in sync
            request_topic = f"device/{PRINTER_SERIAL}/request"
            client.publish(
                request_topic,
                json.dumps({"pushing": {"command": "pushall"}}),
            )
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
