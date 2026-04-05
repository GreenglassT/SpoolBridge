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

from config import (
    load_env, TRAY_EXTERNAL,
    get_openspool_type, get_filament_code, tray_location,
    extra_quote, extra_unquote,
)

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
last_gcode_state = ""      # track state transitions to fire finish only once
bridge_state_path = Path(__file__).parent / "bridge_state.json"
unknown_spools = {}        # {tray_key: {type, color, tag_uid, tray_num, tray}}
ams_environment = {}       # {humidity, temp}
_bridge_state_dirty = True # write bridge_state.json only when state changed
mqtt_client = None          # set in main(); used by publish_external_filament()
bridge_commands_path = Path(__file__).parent / "bridge_commands.json"



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


def find_spool_by_color_material(color_hex, material, spools=None):
    """Find a Spoolman spool matching color + material type."""
    color_hex = color_hex.lower().rstrip("ff")[:6]
    material = material.strip()

    if spools is None:
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


def find_spool_by_tag(tag_uid, tray_uuid="", spools=None):
    """Find a Spoolman spool by RFID tag UID or Bambu tray UUID."""
    if not tag_uid and not tray_uuid:
        return None
    if spools is None:
        spools = sm_get("spool")
    for s in spools:
        extra = s.get("extra", {})
        tag = extra_unquote(extra.get("tag", ""))
        if tag and (tag == tag_uid or tag == tray_uuid):
            return s
    return None


def update_spool_tray(spool_id, tray_name):
    """Set which tray a spool is in."""
    try:
        sm_patch(f"spool/{spool_id}", {
            "extra": {"active_tray": extra_quote(tray_name)},
            "location": tray_location(tray_name),
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


def publish_external_filament(spool):
    """Publish MQTT command to configure the printer's external tray."""
    f = spool.get("filament", {})
    material = (f.get("material") or "PLA").strip()
    color_hex = (f.get("color_hex") or "000000").lower()
    tray_type = get_openspool_type(material)
    filament_code = get_filament_code(tray_type)

    nozzle_min = f.get("settings_extruder_temp") or 190
    nozzle_max = nozzle_min + 20 if f.get("settings_extruder_temp") else 220

    tray_color = (color_hex[:6] + "FF").upper()

    payload = {
        "print": {
            "sequence_id": "0",
            "command": "ams_filament_setting",
            "ams_id": 255,
            "tray_id": 254,
            "tray_color": tray_color,
            "nozzle_temp_min": nozzle_min,
            "nozzle_temp_max": nozzle_max,
            "tray_type": tray_type,
            "setting_id": "",
            "tray_info_idx": filament_code,
        }
    }

    request_topic = f"device/{PRINTER_SERIAL}/request"
    mqtt_client.publish(request_topic, json.dumps(payload))
    log.info(
        f"Published external filament: {tray_type} #{color_hex} "
        f"({nozzle_min}-{nozzle_max}°C, code={filament_code})"
    )


# ============================================================
# EVENT LOGGING
# ============================================================

SCRIPT_DIR = Path(__file__).parent


_activity_line_count = 0

def log_event(event_type, tray="", spool_id=None, spool_name="", detail=""):
    """Append an event to activity.json (JSON-lines, capped at 200 entries)."""
    global _activity_line_count
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
        # Count lines on first call
        if _activity_line_count == 0 and path.exists():
            _activity_line_count = sum(1 for _ in open(path))
        # Append-only most of the time; trim only when over cap
        with open(path, "a") as f:
            f.write(entry + "\n")
        _activity_line_count += 1
        if _activity_line_count > 250:
            lines = path.read_text().strip().splitlines()[-200:]
            path.write_text("\n".join(lines) + "\n")
            _activity_line_count = len(lines)
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


def write_bridge_state():
    """Write current bridge state to a JSON file for the web server to read."""
    global _bridge_state_dirty
    if not _bridge_state_dirty:
        return
    _bridge_state_dirty = False
    state = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "connected": True,
        "ams_environment": ams_environment,
        "unknown_spools": list(unknown_spools.values()),
        "current_vt_tray": current_vt_tray,
    }
    try:
        bridge_state_path.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.error(f"Failed to write bridge_state.json: {e}")


def process_commands():
    """Check for and execute pending commands from the web server."""
    tmp = bridge_commands_path.with_suffix(".processing")
    try:
        bridge_commands_path.rename(tmp)
    except FileNotFoundError:
        return

    try:
        raw = tmp.read_text().strip()
        tmp.unlink(missing_ok=True)
        if not raw:
            return

        cmd = json.loads(raw)
        if cmd.get("command") == "load_external":
            spool_id = cmd.get("spool_id")
            if not spool_id:
                log.error("load_external command missing spool_id")
                return
            try:
                spool = sm_get(f"spool/{spool_id}")
            except Exception as e:
                log.error(f"Failed to fetch spool #{spool_id}: {e}")
                return
            publish_external_filament(spool)
            update_spool_tray(spool_id, TRAY_EXTERNAL)
            f = spool.get("filament", {})
            log_event(
                "external_load",
                tray=TRAY_EXTERNAL,
                spool_id=spool_id,
                spool_name=f.get("name", "?"),
                detail="Loaded via API"
            )
        else:
            log.warning(f"Unknown command: {cmd.get('command')}")
    except json.JSONDecodeError:
        log.error("Invalid JSON in bridge_commands.json")
    except Exception as e:
        log.error(f"Error processing command: {e}")


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
                    "tray_uuid": tray.get("tray_uuid", ""),
                    "remain": tray.get("remain", -1),
                }

    return trays


def parse_ams_environment(msg_data):
    """Extract AMS humidity and temperature from MQTT report."""
    ams_list = msg_data.get("print", {}).get("ams", {}).get("ams", [])
    if not ams_list:
        return None
    unit = ams_list[0]
    humidity = unit.get("humidity", "")
    temp = unit.get("temp", "")
    if humidity or temp:
        return {"humidity": humidity, "temp": temp}
    return None


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
    global current_vt_tray, active_spool_id, _bridge_state_dirty

    if new_vt == current_vt_tray:
        return

    old_vt = current_vt_tray
    current_vt_tray = new_vt
    _bridge_state_dirty = True

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
        update_spool_tray(spool["id"], TRAY_EXTERNAL)
        log_event("external_tap", tray=TRAY_EXTERNAL, spool_id=spool["id"], spool_name=f.get("name", "?"), detail=f"{f.get('material', '?')} #{new_vt['color']}")
    else:
        active_spool_id = None
        log.warning(
            f"No Spoolman spool matches {new_vt['type']} #{new_vt['color']}. "
            f"Create it in Spoolman first."
        )
        log_event("external_tap", tray=TRAY_EXTERNAL, detail=f"Unmatched: {new_vt['type']} #{new_vt['color']}")


def handle_ams_change(new_trays):
    """Handle AMS tray changes (Bambu RFID detection)."""
    global current_ams_trays, _bridge_state_dirty

    # Fetch spools once for all tray lookups (avoids N+1 API calls)
    changed = [k for k, v in new_trays.items() if current_ams_trays.get(k) != v]
    if not changed:
        return
    all_spools = sm_get("spool")

    for slot_key in changed:
        tray_data = new_trays[slot_key]

        tag_uid = tray_data.get("tag_uid", "")
        tray_uuid = tray_data.get("tray_uuid", "")
        tray_num = int(slot_key.split("_")[1]) + 1

        if tag_uid or tray_uuid:
            spool = find_spool_by_tag(tag_uid, tray_uuid, spools=all_spools)
            if spool:
                f = spool.get("filament", {})
                log.info(
                    f"AMS Tray {tray_num}: {f.get('name', '?')} "
                    f"(spool #{spool['id']}, tag {tag_uid[:8]}...)"
                )
                update_spool_tray(spool["id"], f"A{tray_num}")
                log_event("tray_load", tray=f"A{tray_num}", spool_id=spool["id"], spool_name=f.get("name", "?"), detail=f"tag {tag_uid[:8]}...")
                unknown_spools.pop(slot_key, None)
            else:
                log.info(
                    f"AMS Tray {tray_num}: Unknown tag {tag_uid[:8]}... "
                    f"({tray_data['type']} #{tray_data['color']})"
                )
                log_event("tray_load", tray=f"A{tray_num}", detail=f"Unknown tag {tag_uid[:8]}... ({tray_data['type']} #{tray_data['color']})")
                unknown_spools[slot_key] = {
                    "tray": f"A{tray_num}",
                    "type": tray_data["type"],
                    "color": tray_data["color"],
                    "tag_uid": tag_uid,
                    "tray_num": tray_num,
                }
        else:
            # Third-party spool in AMS, no RFID — try color+material match
            spool = find_spool_by_color_material(tray_data["color"], tray_data["type"], spools=all_spools)
            if spool:
                f = spool.get("filament", {})
                log.info(
                    f"AMS Tray {tray_num}: Matched by color → "
                    f"{f.get('name', '?')} (spool #{spool['id']})"
                )
                update_spool_tray(spool["id"], f"A{tray_num}")
                log_event("tray_load", tray=f"A{tray_num}", spool_id=spool["id"], spool_name=f.get("name", "?"), detail="color match")
            else:
                log.info(f"AMS Tray {tray_num}: No match for {tray_data['type']} #{tray_data['color']}")
                log_event("tray_load", tray=f"A{tray_num}", detail=f"Unmatched: {tray_data['type']} #{tray_data['color']}")

        current_ams_trays[slot_key] = tray_data
        _bridge_state_dirty = True


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
        except Exception as e:
            log.warning(f"Could not fetch spool #{active_spool_id} for print log: {e}")

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
    try:
        bridge_state_path.write_text(json.dumps({"connected": False, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    except Exception as e:
        log.warning(f"Could not write disconnect state: {e}")


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

    # Parse AMS environment
    global ams_environment, _bridge_state_dirty
    env = parse_ams_environment(data)
    if env and env != ams_environment:
        ams_environment = env
        _bridge_state_dirty = True

    # Write shared state for web server
    write_bridge_state()

    # Check for print completion — only fire on state *transition* to FINISH/FAILED
    global last_gcode_state
    gcode_state = data.get("print", {}).get("gcode_state", "")
    if gcode_state and gcode_state != last_gcode_state:
        old_state = last_gcode_state
        last_gcode_state = gcode_state
        if gcode_state in ("FINISH", "FAILED") and old_state not in ("FINISH", "FAILED"):
            finish = parse_print_finish(data)
            if finish:
                handle_print_finish(finish)


# ============================================================
# MAIN
# ============================================================

def main():
    global mqtt_client
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
    mqtt_client = mqtt.Client(
        client_id=f"openspool-bridge-{int(time.time())}",
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
    )
    mqtt_client.username_pw_set(MQTT_USER, PRINTER_ACCESS_CODE)

    # TLS without cert verification (printer uses self-signed cert)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    mqtt_client.tls_set_context(ctx)

    mqtt_client.on_connect = on_connect
    mqtt_client.on_disconnect = on_disconnect
    mqtt_client.on_message = on_message

    # Connect and loop
    try:
        mqtt_client.connect(PRINTER_IP, MQTT_PORT, keepalive=60)
    except Exception as e:
        log.error(f"Cannot connect to printer MQTT: {e}")
        sys.exit(1)

    # Run forever, periodically requesting status updates
    mqtt_client.loop_start()

    try:
        poll_counter = 0
        while True:
            process_commands()
            time.sleep(2)
            poll_counter += 1
            if poll_counter >= 15:  # pushall every 30s (15 * 2s)
                poll_counter = 0
                request_topic = f"device/{PRINTER_SERIAL}/request"
                mqtt_client.publish(
                    request_topic,
                    json.dumps({"pushing": {"command": "pushall"}}),
                )
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()


if __name__ == "__main__":
    main()
