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
    extra_quote, extra_unquote, is_real_tag,
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
current_ams_trays = {}     # {slot_id: {type, color, tag_uid, tray_uuid}}
_ams_initialized = False   # True after first pushall seeds the baseline
active_spool_id = None     # Spoolman spool ID currently printing
active_print_name = ""     # subtask_name of the current print job
last_gcode_state = ""      # track state transitions to fire finish only once
DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent))
bridge_state_path = DATA_DIR / "bridge_state.json"
unknown_spools = {}        # {tray_key: {type, color, tag_uid, tray_num, tray}}
duplicate_tag_candidates = {}  # {slot_key: {tray, spool_id, spool_name, new_tag, type, color, tray_num}}
ams_environment = {}       # {humidity, temp}
ams_tray_remain = {}       # {slot_key: remain%} — updated separately from change detection
_last_synced_remain = {}   # {slot_key: remain%} — last value written to Spoolman
printer_telemetry = {}     # live print status (progress, temps, fans, speed, etc.)
print_start_time = None    # epoch when gcode_state first became RUNNING
print_start_weight = None  # spool remaining_weight (g) when print started
_bridge_state_dirty = True # write bridge_state.json only when state changed
mqtt_client = None          # set in main(); used by publish_external_filament()
_last_own_publish_time = 0  # epoch; skip our own ams_filament_setting echoes
# Window for suppressing our own echoes on the request topic. The printer
# broadcasts ams_filament_setting back to subscribers, so we ignore anything
# that arrives within this many seconds of our own publish.
ECHO_SUPPRESS_SECONDS = 2
bridge_commands_path = DATA_DIR / "bridge_commands.json"



# ============================================================
# SPOOLMAN API HELPERS
# ============================================================

def sm_get(endpoint):
    """GET from Spoolman API."""
    r = requests.get(f"{SPOOLMAN_URL}/api/v1/{endpoint}")
    r.raise_for_status()
    return r.json()


def ensure_extra_fields():
    """Register required extra fields in Spoolman if not already present."""
    required = ["tag", "tag2", "active_tray"]
    try:
        existing = requests.get(f"{SPOOLMAN_URL}/api/v1/field/spool").json()
        existing_names = {f["name"] for f in existing}
        for name in required:
            if name not in existing_names:
                requests.post(
                    f"{SPOOLMAN_URL}/api/v1/field/spool/{name}",
                    json={"name": name, "field_type": "text"},
                )
                log.info(f"Registered Spoolman extra field: {name}")
    except Exception as e:
        log.warning(f"Could not register extra fields: {e}")


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
    if not is_real_tag(tag_uid) and not is_real_tag(tray_uuid):
        return None
    if spools is None:
        spools = sm_get("spool")
    for s in spools:
        extra = s.get("extra", {})
        tag = extra_unquote(extra.get("tag", ""))
        tag2 = extra_unquote(extra.get("tag2", ""))
        for stored in (tag, tag2):
            if stored and (stored == tag_uid or stored == tray_uuid):
                return s
    return None


def find_duplicate_tag_candidate(material, color_hex, tag_uid, spools=None):
    """Find an unassigned Bambu spool that might be the same physical spool loaded from the other side.

    Matches by material + color_hex (ignoring sub_brand) so that a Matte spool
    loaded from the other side still gets caught even if the printer reports
    a different sub_brand than the original load.

    Returns a spool if it has:
    - The same material and color hex (prefix-matched for material)
    - An existing RFID tag (confirming it's a Bambu spool)
    - A different tag than the one we're looking for
    - No active tray assignment
    """
    if spools is None:
        spools = sm_get("spool")
    color_hex = (color_hex or "").lower()[:6]
    material = (material or "").lower()
    for s in spools:
        f = s.get("filament", {})
        f_mat = (f.get("material") or "").lower()
        f_color = (f.get("color_hex") or "").lower()
        if f_color != color_hex:
            continue
        if not (f_mat == material or f_mat.startswith(material) or material.startswith(f_mat)):
            continue
        extra = s.get("extra", {})
        stored_tag = extra_unquote(extra.get("tag", ""))
        if not is_real_tag(stored_tag):
            continue
        if stored_tag == tag_uid:
            continue
        active_tray = extra_unquote(extra.get("active_tray", ""))
        if active_tray:
            continue
        # Skip empty spools — likely a used-up spool being replaced by a refill
        if s.get("remaining_weight", 0) <= 0:
            continue
        return s
    return None


# Bambu Lab color hex → name mapping, keyed by (hex, sub_brand_keyword).
# Sub-brand keywords disambiguate shared hex codes (e.g. 000000 is "Black"
# for Basic but "Charcoal" for Matte).  Lookup order: exact (hex, sub_brand)
# match first, then hex-only fallback.
_BAMBU_COLORS_BY_LINE = {
    # PLA Basic (source: official Bambu Lab Hex Code Table PDF)
    ("ffffff", "basic"): "Jade White",
    ("000000", "basic"): "Black",
    ("f7e6de", "basic"): "Beige",
    ("d1d3d5", "basic"): "Light Gray",
    ("a6a9aa", "basic"): "Silver",
    ("8e9089", "basic"): "Gray",
    ("545454", "basic"): "Dark Gray",
    ("ec008c", "basic"): "Magenta",
    ("f55a74", "basic"): "Pink",
    ("f5547c", "basic"): "Hot Pink",
    ("ff6a13", "basic"): "Orange",
    ("ff9016", "basic"): "Pumpkin Orange",
    ("e4bd68", "basic"): "Gold",
    ("fec600", "basic"): "Sunflower Yellow",
    ("f4ee2a", "basic"): "Yellow",
    ("becf00", "basic"): "Bright Green",
    ("00ae42", "basic"): "Bambu Green",
    ("3f8e43", "basic"): "Mistletoe Green",
    ("847d48", "basic"): "Bronze",
    ("6f5034", "basic"): "Cocoa Brown",
    ("9d432c", "basic"): "Brown",
    ("9d2235", "basic"): "Maroon Red",
    ("c12e1f", "basic"): "Red",
    ("00b1b7", "basic"): "Turquoise",
    ("0086d6", "basic"): "Cyan",
    ("0a2989", "basic"): "Blue",
    ("0056b8", "basic"): "Cobalt Blue",
    ("5e43b7", "basic"): "Purple",
    ("482960", "basic"): "Indigo Purple",
    ("5b6579", "basic"): "Blue Grey",
    # PLA Matte (source: official Bambu Lab Hex Code Table PDF)
    ("ffffff", "matte"): "Ivory White",
    ("000000", "matte"): "Charcoal",
    ("cbc6b8", "matte"): "Bone White",
    ("e8dbb7", "matte"): "Desert Tan",
    ("d3b7a7", "matte"): "Latte Brown",
    ("ae835b", "matte"): "Caramel",
    ("b15533", "matte"): "Terracotta",
    ("7d6556", "matte"): "Dark Brown",
    ("4d3324", "matte"): "Dark Chocolate",
    ("ae96d4", "matte"): "Lilac Purple",
    ("e8afcf", "matte"): "Sakura Pink",
    ("f99963", "matte"): "Mandarin Orange",
    ("f7d959", "matte"): "Lemon Yellow",
    ("950051", "matte"): "Plum",
    ("de4343", "matte"): "Scarlet Red",
    ("bb3d43", "matte"): "Dark Red",
    ("68724d", "matte"): "Dark Green",
    ("61c680", "matte"): "Grass Green",
    ("c2e189", "matte"): "Apple Green",
    ("a3d8e1", "matte"): "Ice Blue",
    ("56b7e6", "matte"): "Sky Blue",
    ("9b9ea0", "matte"): "Ash Gray",
    ("757575", "matte"): "Nardo Gray",
    ("0078bf", "matte"): "Marine Blue",
    ("042f56", "matte"): "Dark Blue",
    # PLA-CF
    ("951e23", "cf"): "Burgundy Red",
    ("69398e", "cf"): "Iris Purple",
    ("5c9748", "cf"): "Matcha Green",
    ("6e88bc", "cf"): "Jeans Blue",
    ("2842ad", "cf"): "Royal Blue",
    ("4d5054", "cf"): "Lava Gray",
    # PETG-CF
    ("9f332a", "cf"): "Brick Red",
    ("583061", "cf"): "Violet Purple",
    ("324585", "cf"): "Indigo Blue",
    ("16b08e", "cf"): "Malachite Green",
    ("565656", "cf"): "Titan Gray",
    # PLA Galaxy
    ("424379", "galaxy"): "Nebulae",
    # PLA Marble
    ("f7f3f0", "marble"): "White",
    # PLA Tough+
    ("afb1ae", "tough"): "Gray",
    ("959698", "tough"): "Silver",
    ("f4d53f", "tough"): "Yellow",
    ("009bd8", "tough"): "Cyan",
    ("dc3a27", "tough"): "Orange",
    # PLA Translucent
    ("009fa1", "translucent"): "Teal",
    ("96d8af", "translucent"): "Light Jade",
    ("0047bb", "translucent"): "Blue",
    ("f5dbab", "translucent"): "Mellow Yellow",
    ("8344b0", "translucent"): "Purple",
    ("f5b6cd", "translucent"): "Cherry Pink",
    ("f74e02", "translucent"): "Orange",
    ("b8cde9", "translucent"): "Ice Blue",
    ("b50011", "translucent"): "Red",
    ("b8acd6", "translucent"): "Lavender",
    # PLA Wood
    ("4f3f24", "wood"): "Black Walnut",
    ("4c241c", "wood"): "Rosewood",
    ("995f11", "wood"): "Clay Brown",
    ("918669", "wood"): "Classic Birch",
    ("d6cca3", "wood"): "White Oak",
    ("c98935", "wood"): "Ochre Yellow",
    # ABS
    ("e8dbb7", "abs"): "Desert Tan",
    ("789d4a", "abs"): "Olive",
    ("489fdf", "abs"): "Azure",
    ("0c2340", "abs"): "Navy Blue",
    ("0a2ca5", "abs"): "Blue",
    ("ffc72c", "abs"): "Tangerine Yellow",
    ("ff6a13", "abs"): "Orange",
    ("d32941", "abs"): "Red",
    ("af1685", "abs"): "Purple",
    ("87909a", "abs"): "Silver",
    # PLA Silk
    ("147bd1", "silk"): "Blue",
    ("854ce4", "silk"): "Purple",
    ("eaeceb", "silk"): "Silver",
    # PLA Silk+
    ("a8c6ee", "silk"): "Baby Blue",
    ("008bda", "silk"): "Blue",
    ("018814", "silk"): "Candy Green",
    ("d02727", "silk"): "Candy Red",
    ("f3cfb2", "silk"): "Champagne",
    ("f4a925", "silk"): "Gold",
    ("96dcb9", "silk"): "Mint",
    ("f7ada6", "silk"): "Pink",
    ("8671cb", "silk"): "Purple",
    ("ba9594", "silk"): "Rose Gold",
    ("c8c8c8", "silk"): "Silver",
    ("5f6367", "silk"): "Titan Gray",
    # ASA
    ("2140b4", "asa"): "Blue",
    ("8a949e", "asa"): "Gray",
    ("00a6a0", "asa"): "Green",
    ("e02928", "asa"): "Red",
    # PLA Galaxy
    ("684a43", "galaxy"): "Brown",
    ("3b665e", "galaxy"): "Green",
    ("594177", "galaxy"): "Purple",
    # PLA Marble
    ("ad4e38", "marble"): "Red Granite",
    # TPU 90A
    ("ffffee", "tpu"): "White",
    ("d6abff", "tpu"): "Grape Jelly",
    ("7eb4e1", "tpu"): "Crystal Blue",
    ("5c4738", "tpu"): "Cocoa Brown",
    ("9ea2a2", "tpu"): "Quicksilver",
    ("f1aaa8", "tpu"): "Blaze",
}

# Hex-only fallback (no sub_brand disambiguation needed)
_BAMBU_COLORS_GENERIC = {
    "000000": "Black",
    "ffffff": "White",
    "f7e6de": "Beige",
    "d1d3d5": "Light Gray",
    "a6a9aa": "Silver",
    "8e9089": "Gray",
    "545454": "Dark Gray",
    "ec008c": "Magenta",
    "f55a74": "Pink",
    "f5547c": "Hot Pink",
    "ff6a13": "Orange",
    "ff9016": "Pumpkin Orange",
    "e4bd68": "Gold",
    "fec600": "Sunflower Yellow",
    "f4ee2a": "Yellow",
    "becf00": "Bright Green",
    "00ae42": "Bambu Green",
    "3f8e43": "Mistletoe Green",
    "847d48": "Bronze",
    "6f5034": "Cocoa Brown",
    "9d432c": "Brown",
    "9d2235": "Maroon Red",
    "c12e1f": "Red",
    "00b1b7": "Turquoise",
    "0086d6": "Cyan",
    "0a2989": "Blue",
    "0056b8": "Cobalt Blue",
    "5e43b7": "Purple",
    "482960": "Indigo Purple",
    "5b6579": "Blue Grey",
    "cbc6b8": "Bone White",
    "e8dbb7": "Desert Tan",
    "d3b7a7": "Latte Brown",
    "ae835b": "Caramel",
    "b15533": "Terracotta",
    "7d6556": "Dark Brown",
    "4d3324": "Dark Chocolate",
    "ae96d4": "Lilac Purple",
    "e8afcf": "Sakura Pink",
    "f99963": "Mandarin Orange",
    "f7d959": "Lemon Yellow",
    "950051": "Plum",
    "de4343": "Scarlet Red",
    "bb3d43": "Dark Red",
    "68724d": "Dark Green",
    "61c680": "Grass Green",
    "c2e189": "Apple Green",
    "9b9ea0": "Ash Gray",
    "0078bf": "Marine Blue",
    "a3d8e1": "Ice Blue",
    "56b7e6": "Sky Blue",
    "757575": "Nardo Gray",
    "042f56": "Dark Blue",
    "951e23": "Burgundy Red",
    "69398e": "Iris Purple",
    "5c9748": "Matcha Green",
    "6e88bc": "Jeans Blue",
    "2842ad": "Royal Blue",
    "4d5054": "Lava Gray",
    "9f332a": "Brick Red",
    "583061": "Violet Purple",
    "324585": "Indigo Blue",
    "16b08e": "Malachite Green",
    "565656": "Titan Gray",
    "424379": "Nebulae",
    "f7f3f0": "Marble White",
    "afb1ae": "Gray",
    "959698": "Silver",
    "f4d53f": "Yellow",
    "009bd8": "Cyan",
    "dc3a27": "Orange",
    "009fa1": "Teal",
    "96d8af": "Light Jade",
    "0047bb": "Blue",
    "f5dbab": "Mellow Yellow",
    "8344b0": "Purple",
    "f5b6cd": "Cherry Pink",
    "f74e02": "Orange",
    "b8cde9": "Ice Blue",
    "b50011": "Red",
    "b8acd6": "Lavender",
    "4f3f24": "Black Walnut",
    "4c241c": "Rosewood",
    "995f11": "Clay Brown",
    "918669": "Classic Birch",
    "d6cca3": "White Oak",
    "c98935": "Ochre Yellow",
    "789d4a": "Olive",
    "489fdf": "Azure",
    "0c2340": "Navy Blue",
    "0a2ca5": "Blue",
    "ffc72c": "Tangerine Yellow",
    "ff6a13": "Orange",
    "d32941": "Red",
    "af1685": "Purple",
    "87909a": "Silver",
    "ffffee": "White",
    "d6abff": "Grape Jelly",
    "7eb4e1": "Crystal Blue",
    "5c4738": "Cocoa Brown",
    "9ea2a2": "Quicksilver",
    "f1aaa8": "Blaze",
    # PLA Silk / Silk+
    "147bd1": "Blue",
    "854ce4": "Purple",
    "eaeceb": "Silver",
    "a8c6ee": "Baby Blue",
    "008bda": "Blue",
    "018814": "Candy Green",
    "d02727": "Candy Red",
    "f3cfb2": "Champagne",
    "f4a925": "Gold",
    "96dcb9": "Mint",
    "f7ada6": "Pink",
    "8671cb": "Purple",
    "ba9594": "Rose Gold",
    "c8c8c8": "Silver",
    "5f6367": "Titan Gray",
    # ASA
    "2140b4": "Blue",
    "8a949e": "Gray",
    "00a6a0": "Green",
    "e02928": "Red",
    # PLA Galaxy
    "684a43": "Brown",
    "3b665e": "Green",
    "594177": "Purple",
    # PLA Marble
    "ad4e38": "Red Granite",
}


def bambu_color_name(color_hex, sub_brand=""):
    """Resolve a Bambu hex color to its marketing name.

    Tries (hex, sub_brand_keyword) first for lines that share hex codes,
    then falls back to the generic hex-only table, then '#RRGGBB'.
    """
    h = color_hex.lower()[:6]
    sb = sub_brand.lower()
    # Try sub-brand-specific lookup
    for keyword in _BAMBU_COLORS_BY_LINE:
        if keyword[0] == h and keyword[1] in sb:
            return _BAMBU_COLORS_BY_LINE[keyword]
    # Also try material-as-keyword for ABS/TPU which don't have sub_brand
    # (handled via generic fallback)
    return _BAMBU_COLORS_GENERIC.get(h, f"#{color_hex}")


def auto_create_bambu_spool(tray_data, tray_name, remain_pct=None,
                            vendors=None, filaments=None):
    """Auto-create a Bambu spool in Spoolman from AMS RFID data."""
    tag_uid = tray_data.get("tag_uid", "")
    tray_uuid = tray_data.get("tray_uuid", "")
    material = tray_data.get("type", "PLA")
    color_hex = tray_data.get("color", "000000")
    sub_brand = tray_data.get("sub_brand", "")
    nozzle_min = tray_data.get("nozzle_min", 190)
    nozzle_max = tray_data.get("nozzle_max", 230)
    net_weight = tray_data.get("net_weight", 1000) or 1000

    color_name = bambu_color_name(color_hex, sub_brand)
    display_name = f"{sub_brand} {color_name}".strip() if sub_brand else f"{material} {color_name}"

    try:
        if vendors is None:
            vendors = sm_get("vendor")
        vendor_id = None
        for v in vendors:
            if v["name"].lower() == "bambu lab":
                vendor_id = v["id"]
                break
        if not vendor_id:
            r = requests.post(f"{SPOOLMAN_URL}/api/v1/vendor",
                              json={"name": "Bambu Lab", "empty_spool_weight": 251.0})
            vendor_id = r.json()["id"]

        if filaments is None:
            filaments = sm_get("filament")
        filament_id = None
        # Match by material + color + vendor, preferring name match when
        # multiple filaments share the same hex (e.g. PLA Basic Black vs
        # PLA Matte Charcoal are both PLA #000000 by Bambu Lab).
        candidates = []
        for f in filaments:
            f_mat = (f.get("material") or "").lower()
            f_color = (f.get("color_hex") or "").lower()
            f_vendor = (f.get("vendor") or {}).get("id")
            if f_mat == material.lower() and f_color == color_hex.lower() and f_vendor == vendor_id:
                candidates.append(f)
        if candidates:
            # Prefer exact name match (e.g. "Matte Charcoal" in "PLA Matte Charcoal")
            for f in candidates:
                f_name = (f.get("name") or "").lower()
                if display_name.lower() in f_name or f_name in display_name.lower():
                    filament_id = f["id"]
                    break
            if not filament_id:
                # No name match — don't reuse a wrong filament, create a new one
                filament_id = None

        if not filament_id:
            fil_data = {
                "name": display_name,
                "vendor_id": vendor_id,
                "material": material,
                "color_hex": color_hex,
                "density": 1.24,
                "diameter": 1.75,
                "weight": net_weight,
                "spool_weight": 251.0,
                "settings_extruder_temp": nozzle_min,
            }
            r = requests.post(f"{SPOOLMAN_URL}/api/v1/filament", json=fil_data)
            filament_id = r.json()["id"]
            log.info(f"Auto-created filament: {display_name} (#{filament_id})")

        # Create spool with tag linked
        remaining = net_weight
        if remain_pct is not None and 0 <= remain_pct <= 100:
            remaining = round(net_weight * remain_pct / 100)
        spool_data = {
            "filament_id": filament_id,
            "remaining_weight": remaining,
            "location": tray_location(tray_name),
        }
        r = requests.post(f"{SPOOLMAN_URL}/api/v1/spool", json=spool_data)
        spool = r.json()
        spool_id = spool["id"]

        # Link the RFID tag
        tag_value = tag_uid or tray_uuid
        sm_patch(f"spool/{spool_id}", {
            "extra": {
                "tag": extra_quote(tag_value),
                "active_tray": extra_quote(tray_name),
            },
        })

        log.info(f"Auto-created spool #{spool_id}: {display_name} → {tray_name} (tag {tag_value[:8]}...)")
        return spool

    except Exception as e:
        log.error(f"Failed to auto-create Bambu spool: {e}")
        return None


def sync_remain_to_spoolman():
    """Sync AMS remaining % to Spoolman when it changes by 2%+."""
    global _last_synced_remain
    if not ams_tray_remain or not current_ams_trays:
        return

    # Check if any tray needs syncing before fetching spools
    needs_sync = any(
        _last_synced_remain.get(t) is None or abs(pct - _last_synced_remain.get(t, 0)) >= 2
        for t, pct in ams_tray_remain.items()
    )
    if not needs_sync:
        return

    all_spools = sm_get("spool")

    for tray_name, pct in ams_tray_remain.items():
        last = _last_synced_remain.get(tray_name)
        if last is not None and abs(pct - last) < 2:
            continue

        tray_idx = int(tray_name[1]) - 1
        slot_key = f"A0_{tray_idx}"
        tray_data = current_ams_trays.get(slot_key)
        if not tray_data:
            continue

        tag_uid = tray_data.get("tag_uid", "")
        tray_uuid = tray_data.get("tray_uuid", "")
        if not is_real_tag(tag_uid) and not is_real_tag(tray_uuid):
            continue

        spool = find_spool_by_tag(tag_uid, tray_uuid, spools=all_spools)
        if not spool:
            continue

        net_weight = (spool.get("filament") or {}).get("weight") or 1000
        new_weight = round(net_weight * pct / 100)
        old_weight = spool.get("remaining_weight", 0)

        try:
            sm_patch(f"spool/{spool['id']}", {"remaining_weight": new_weight})
            _last_synced_remain[tray_name] = pct
            log.info(f"Synced {tray_name} weight: {old_weight:.0f}g → {new_weight}g ({pct}%)")
            # Log consumption for usage history chart
            used = old_weight - new_weight
            if used > 0:
                f = spool.get("filament") or {}
                log_usage(
                    spool_id=spool["id"],
                    spool_name=f.get("name", "?"),
                    material=f.get("material", "?"),
                    color_hex=f.get("color_hex", "000000"),
                    grams_used=used,
                    tray=tray_name,
                )
        except Exception as e:
            log.warning(f"Failed to sync {tray_name} weight: {e}")


def update_spool_tray(spool_id, tray_name, all_spools=None):
    """Set which tray a spool is in, evicting any other spool from the same tray."""
    try:
        # Clear active_tray from any other spool currently assigned to this tray
        for s in (all_spools if all_spools is not None else sm_get("spool")):
            if s["id"] == spool_id:
                continue
            existing_tray = extra_unquote(
                (s.get("extra") or {}).get("active_tray", "")
            )
            if existing_tray == tray_name:
                sm_patch(f"spool/{s['id']}", {
                    "extra": {"active_tray": extra_quote("")},
                })
                log.info(f"Evicted spool #{s['id']} from {tray_name}")
        sm_patch(f"spool/{spool_id}", {
            "extra": {"active_tray": extra_quote(tray_name)},
            "location": tray_location(tray_name),
        })
        log.info(f"Spool #{spool_id} → {tray_name}")
    except Exception as e:
        log.error(f"Failed to update spool #{spool_id}: {e}")


def clear_tray_assignment(tray_name, all_spools=None):
    """Clear active_tray from any spool assigned to the given tray."""
    for s in (all_spools if all_spools is not None else sm_get("spool")):
        existing_tray = extra_unquote(
            (s.get("extra") or {}).get("active_tray", "")
        )
        if existing_tray == tray_name:
            sm_patch(f"spool/{s['id']}", {
                "extra": {"active_tray": extra_quote("")},
            })
            log.info(f"Cleared spool #{s['id']} from {tray_name} (slot now empty)")


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


def publish_filament_setting(spool, ams_id=255, tray_id=254):
    """Publish MQTT command to configure a printer tray's filament settings.

    For the external tray use ams_id=255, tray_id=254 (default).
    For AMS trays use ams_id=0, tray_id=0-3.
    """
    global _last_own_publish_time
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
            "ams_id": ams_id,
            "tray_id": tray_id,
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
    _last_own_publish_time = time.time()
    tray_label = "External" if ams_id == 255 else f"A{tray_id + 1}"
    log.info(
        f"Published filament to {tray_label}: {tray_type} #{color_hex} "
        f"({nozzle_min}-{nozzle_max}°C, code={filament_code})"
    )


def publish_external_filament(spool):
    """Publish MQTT command to configure the printer's external tray."""
    publish_filament_setting(spool, ams_id=255, tray_id=254)


# ============================================================
# EVENT LOGGING
# ============================================================

SCRIPT_DIR = DATA_DIR

_jsonl_counts = {}

def _append_jsonl(filename, record, cap):
    """Append a JSON-lines record to a file, trimming to cap when exceeded."""
    try:
        path = SCRIPT_DIR / filename
        record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        entry = json.dumps(record)
        if filename not in _jsonl_counts and path.exists():
            _jsonl_counts[filename] = sum(1 for _ in open(path))
        with open(path, "a") as f:
            f.write(entry + "\n")
        _jsonl_counts[filename] = _jsonl_counts.get(filename, 0) + 1
        if _jsonl_counts[filename] > cap:
            keep = int(cap * 0.8)  # trim to 80% of cap
            lines = path.read_text().strip().splitlines()[-keep:]
            path.write_text("\n".join(lines) + "\n")
            _jsonl_counts[filename] = len(lines)
    except Exception as e:
        log.error(f"Failed to write {filename}: {e}")


def log_event(event_type, tray="", spool_id=None, spool_name="", detail=""):
    """Append an event to activity.json (capped at 250 entries)."""
    _append_jsonl("activity.json", {
        "type": event_type, "tray": tray,
        "spool_id": spool_id, "spool_name": spool_name, "detail": detail,
    }, cap=250)


def log_usage(spool_id, spool_name, material, color_hex, grams_used, tray=""):
    """Log filament consumption to usage.json (from AMS weight sync)."""
    _append_jsonl("usage.json", {
        "spool_id": spool_id, "spool_name": spool_name,
        "material": material, "color_hex": color_hex,
        "grams_used": round(grams_used, 1), "tray": tray,
    }, cap=1000)


def log_print(spool_id, spool_name, material, color_hex, grams_used, status,
              print_name="", duration=0):
    """Append a print record to prints.json (capped at 500 entries)."""
    _append_jsonl("prints.json", {
        "spool_id": spool_id, "spool_name": spool_name,
        "material": material, "color_hex": color_hex,
        "grams_used": grams_used, "status": status,
        "print_name": print_name, "duration": duration,
    }, cap=500)


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
        "ams_tray_remain": ams_tray_remain,
        "unknown_spools": list(unknown_spools.values()),
        "duplicate_tag_candidates": list(duplicate_tag_candidates.values()),
        "current_vt_tray": current_vt_tray,
        "printer": printer_telemetry,
        "print_start_time": print_start_time,
        "print_start_weight": print_start_weight,
        "active_spool_id": active_spool_id,
        "active_print_name": active_print_name,
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
        "color": tray_color[:6].upper() if tray_color else "",
        "nozzle_min": vt.get("nozzle_temp_min", 0),
        "nozzle_max": vt.get("nozzle_temp_max", 0),
    }


def parse_request_filament_setting(msg_data):
    """Parse ams_filament_setting from the request topic (OpenSpool tag reads)."""
    p = msg_data.get("print", {})
    if p.get("command") != "ams_filament_setting" or p.get("ams_id") != 255:
        return None
    tray_color = p.get("tray_color", "")
    tray_type = p.get("tray_type", "")
    if not tray_type:
        return None
    return {
        "type": tray_type,
        "color": tray_color[:6].upper() if tray_color else "",
        "nozzle_min": p.get("nozzle_temp_min", 0),
        "nozzle_max": p.get("nozzle_temp_max", 0),
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

            trays[slot_key] = {
                "type": tray_type,
                "color": (tray.get("tray_color") or "")[:6],
                "tag_uid": tray.get("tag_uid", ""),
                "tray_uuid": tray.get("tray_uuid", ""),
                "sub_brand": tray.get("tray_sub_brands", ""),
                "nozzle_min": int(tray.get("nozzle_temp_min", 0)),
                "nozzle_max": int(tray.get("nozzle_temp_max", 0)),
                "net_weight": int(tray.get("tray_weight", 0)),
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


def parse_active_tray(msg_data):
    """Track which AMS tray is actively feeding during a print."""
    mc_print = msg_data.get("print", {})
    gcode_state = mc_print.get("gcode_state", "")
    if gcode_state != "RUNNING":
        return None

    # tray_now is the AMS tray index currently feeding (0-3)
    ams_data = mc_print.get("ams", {})
    tray_now = ams_data.get("tray_now")
    subtask_name = mc_print.get("subtask_name", "")

    return {"tray_now": tray_now, "subtask_name": subtask_name}


def parse_ams_remain(msg_data):
    """Extract AMS tray remaining % (updated separately from identity fields)."""
    ams_list = msg_data.get("print", {}).get("ams", {}).get("ams", [])
    result = {}
    for ams_unit in ams_list:
        ams_id = ams_unit.get("id", "0")
        for tray in ams_unit.get("tray", []):
            tray_id = tray.get("id", "0")
            remain = tray.get("remain", -1)
            if remain >= 0:
                tray_num = int(tray_id) + 1
                result[f"A{tray_num}"] = remain
    return result


def parse_printer_telemetry(msg_data):
    """Extract live printer telemetry from MQTT report."""
    mc = msg_data.get("print", {})
    gcode_state = mc.get("gcode_state", "")
    if not gcode_state:
        return None

    ams_data = mc.get("ams", {})
    tray_now = ams_data.get("tray_now")
    # Map tray index to tray name
    if tray_now == "255":
        tray_name = "External"
    elif tray_now is not None:
        try:
            tray_name = f"A{int(tray_now) + 1}"
        except (ValueError, TypeError):
            tray_name = None
    else:
        tray_name = None

    return {
        "gcode_state": gcode_state,
        "subtask_name": mc.get("subtask_name", ""),
        "percent": mc.get("mc_percent") or mc.get("percent", 0),
        "remain_time": mc.get("mc_remaining_time") or mc.get("remain_time", 0),
        "layer_num": mc.get("layer_num", 0),
        "total_layer_num": mc.get("total_layer_num", 0),
        "nozzle_temper": mc.get("nozzle_temper", 0),
        "nozzle_target": mc.get("nozzle_target_temper", 0),
        "bed_temper": mc.get("bed_temper", 0),
        "bed_target": mc.get("bed_target_temper", 0),
        "cooling_fan": mc.get("cooling_fan_speed", "0"),
        "big_fan1": mc.get("big_fan1_speed", "0"),
        "big_fan2": mc.get("big_fan2_speed", "0"),
        "heatbreak_fan": mc.get("heatbreak_fan_speed", "0"),
        "spd_lvl": mc.get("spd_lvl", 0),
        "spd_mag": mc.get("spd_mag", 100),
        "wifi_signal": mc.get("wifi_signal", ""),
        "tray_now": tray_name,
        "print_type": mc.get("print_type", ""),
        "gcode_file": mc.get("gcode_file", ""),
    }


def parse_print_finish(msg_data):
    """Check if a print just finished and get filament usage."""
    mc_print = msg_data.get("print", {})

    # Print state transitions
    gcode_state = mc_print.get("gcode_state", "")
    if gcode_state != "FINISH" and gcode_state != "FAILED":
        return None

    status = gcode_state.lower()  # "finish" or "failed"
    return {"status": status}


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


def _filament_id_for_tray(tray_data, cached_filaments=None):
    """Look up the Spoolman filament ID for the given tray's material+color+sub_brand."""
    material = tray_data.get("type", "")
    color = tray_data.get("color", "000000").lower()[:6]
    sub_brand = tray_data.get("sub_brand", "")
    if not material:
        return None
    filaments = cached_filaments if cached_filaments is not None else sm_get("filament")
    # Build expected display name for sub_brand matching
    color_name = bambu_color_name(color, sub_brand)
    display_name = f"{sub_brand} {color_name}".strip() if sub_brand else f"{material} {color_name}"
    candidates = []
    for f in filaments:
        f_mat = (f.get("material") or "").strip()
        f_color = (f.get("color_hex") or "").lower()
        mat_match = (
            f_mat.lower() == material.lower()
            or f_mat.lower().startswith(material.lower())
            or material.lower().startswith(f_mat.lower())
        )
        if f_color == color and mat_match:
            candidates.append(f)
    if not candidates:
        return None
    # Prefer name match when multiple filaments share the same hex
    for f in candidates:
        f_name = (f.get("name") or "").lower()
        if display_name.lower() in f_name or f_name in display_name.lower():
            return f["id"]
    # Single candidate is safe; multiple without name match is ambiguous
    if len(candidates) == 1:
        return candidates[0]["id"]
    return None


def handle_ams_change(new_trays):
    """Handle AMS tray changes (Bambu RFID detection)."""
    global current_ams_trays, _ams_initialized, _bridge_state_dirty

    # First pushall after startup: seed baseline and auto-create missing spools
    if not _ams_initialized:
        _ams_initialized = True
        current_ams_trays = dict(new_trays)
        _bridge_state_dirty = True
        log.info(f"AMS baseline: {len(new_trays)} trays loaded")

        # Auto-create any Bambu spools not yet in Spoolman, clear empty slots
        all_spools = sm_get("spool")
        cached_vendors = sm_get("vendor")
        cached_filaments = sm_get("filament")
        for slot_key, tray_data in new_trays.items():
            tray_num = int(slot_key.split("_")[1]) + 1
            tray_name = f"A{tray_num}"

            # Empty slot — clear any stale Spoolman assignment
            if not tray_data.get("type"):
                clear_tray_assignment(tray_name, all_spools=all_spools)
                continue

            tag_uid = tray_data.get("tag_uid", "")
            tray_uuid = tray_data.get("tray_uuid", "")
            if is_real_tag(tag_uid) or is_real_tag(tray_uuid):
                spool = find_spool_by_tag(tag_uid, tray_uuid, spools=all_spools)
                if not spool:
                    tag_value = tag_uid or tray_uuid
                    tray_material = tray_data.get("type", "")
                    tray_color = tray_data.get("color", "")[:6]
                    dup = find_duplicate_tag_candidate(tray_material, tray_color, tag_value, spools=all_spools) if tray_material else None
                    if dup:
                        dup_name = (dup.get("filament") or {}).get("name", "?")
                        duplicate_tag_candidates[slot_key] = {
                            "tray": tray_name,
                            "spool_id": dup["id"],
                            "spool_name": dup_name,
                            "new_tag": tag_value,
                            "type": tray_data.get("type", ""),
                            "color": tray_data.get("color", ""),
                            "tray_num": tray_num,
                        }
                        log.info(f"AMS Tray {tray_num}: duplicate tag candidate — spool #{dup['id']} ({dup_name})")
                        log_event("duplicate_tag", tray=tray_name, spool_id=dup["id"], spool_name=dup_name, detail=f"new tag {tag_value[:8]}...")
                    else:
                        remain = ams_tray_remain.get(tray_name)
                        spool = auto_create_bambu_spool(
                            tray_data, tray_name, remain_pct=remain,
                            vendors=cached_vendors, filaments=cached_filaments,
                        )
                        if spool:
                            all_spools.append(spool)
                else:
                    update_spool_tray(spool["id"], tray_name, all_spools=all_spools)
        return

    # Compare only identity fields (not nozzle temps or net_weight which can fluctuate)
    identity_keys = ("type", "color", "tag_uid", "tray_uuid")
    changed = [k for k, v in new_trays.items()
               if any(v.get(f) != (current_ams_trays.get(k) or {}).get(f) for f in identity_keys)]
    if not changed:
        return
    all_spools = sm_get("spool")

    for slot_key in changed:
        tray_data = new_trays[slot_key]
        tray_num = int(slot_key.split("_")[1]) + 1
        tray_name = f"A{tray_num}"

        # Slot is now empty — clear any Spoolman assignment
        if not tray_data.get("type"):
            log.info(f"AMS Tray {tray_num}: now empty")
            clear_tray_assignment(tray_name, all_spools=all_spools)
            log_event("tray_unload", tray=tray_name, detail="slot empty")
            unknown_spools.pop(slot_key, None)
            duplicate_tag_candidates.pop(slot_key, None)
            current_ams_trays[slot_key] = tray_data
            _bridge_state_dirty = True
            continue

        tag_uid = tray_data.get("tag_uid", "")
        tray_uuid = tray_data.get("tray_uuid", "")

        if is_real_tag(tag_uid) or is_real_tag(tray_uuid):
            spool = find_spool_by_tag(tag_uid, tray_uuid, spools=all_spools)
            dup = None
            if not spool:
                tag_value = tag_uid or tray_uuid
                tray_material = tray_data.get("type", "")
                tray_color = tray_data.get("color", "")[:6]
                dup = find_duplicate_tag_candidate(tray_material, tray_color, tag_value, spools=all_spools) if tray_material else None
                if dup:
                    dup_name = (dup.get("filament") or {}).get("name", "?")
                    duplicate_tag_candidates[slot_key] = {
                        "tray": tray_name,
                        "spool_id": dup["id"],
                        "spool_name": dup_name,
                        "new_tag": tag_value,
                        "type": tray_data.get("type", ""),
                        "color": tray_data.get("color", ""),
                        "tray_num": tray_num,
                    }
                    log.info(f"AMS Tray {tray_num}: duplicate tag candidate — spool #{dup['id']} ({dup_name})")
                    log_event("duplicate_tag", tray=tray_name, spool_id=dup["id"], spool_name=dup_name, detail=f"new tag {tag_value[:8]}...")
                    unknown_spools.pop(slot_key, None)
                else:
                    # Auto-create Bambu spool from RFID data
                    remain = ams_tray_remain.get(tray_name)
                    spool = auto_create_bambu_spool(tray_data, tray_name, remain_pct=remain)
            if spool:
                f = spool.get("filament", {})
                log.info(
                    f"AMS Tray {tray_num}: {f.get('name', '?')} "
                    f"(spool #{spool['id']}, tag {tag_uid[:8]}...)"
                )
                update_spool_tray(spool["id"], tray_name, all_spools=all_spools)
                log_event("tray_load", tray=tray_name, spool_id=spool["id"], spool_name=f.get("name", "?"), detail=f"tag {tag_uid[:8]}...")
                unknown_spools.pop(slot_key, None)
                duplicate_tag_candidates.pop(slot_key, None)
            elif not dup:
                log.info(
                    f"AMS Tray {tray_num}: Failed to create spool for tag {tag_uid[:8]}... "
                    f"({tray_data['type']} #{tray_data['color']})"
                )
                log_event("tray_load", tray=tray_name, detail=f"Unknown tag {tag_uid[:8]}... ({tray_data['type']} #{tray_data['color']})")
                unknown_spools[slot_key] = {
                    "tray": tray_name,
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
                update_spool_tray(spool["id"], tray_name, all_spools=all_spools)
                log_event("tray_load", tray=tray_name, spool_id=spool["id"], spool_name=f.get("name", "?"), detail="color match")
            else:
                log.info(f"AMS Tray {tray_num}: No match for {tray_data['type']} #{tray_data['color']}")
                log_event("tray_load", tray=tray_name, detail=f"Unmatched: {tray_data['type']} #{tray_data['color']}")

        current_ams_trays[slot_key] = tray_data
        _bridge_state_dirty = True


def handle_print_finish(finish_data):
    """Handle print completion — update filament weight and log events."""
    global active_spool_id, active_print_name, print_start_time, print_start_weight

    status = finish_data.get("status", "finish")
    print_name = active_print_name
    duration = int(time.time() - print_start_time) if print_start_time else 0

    # Final sync of AMS percentages so Spoolman has the latest weight
    sync_remain_to_spoolman()

    # Calculate grams used from the weight difference (start vs current)
    used = 0.0
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
            current_weight = spool.get("remaining_weight", 0)
            if print_start_weight is not None and print_start_weight > current_weight:
                used = round(print_start_weight - current_weight, 1)
                log.info(f"Print {status}: spool #{active_spool_id} {print_start_weight:.0f}g → {current_weight:.0f}g ({used:.1f}g used)")
            else:
                log.info(f"Print {status}: spool #{active_spool_id} (no weight change detected)")
        except Exception as e:
            log.warning(f"Could not fetch spool #{active_spool_id} for print log: {e}")

    # Skip logging empty records (no spool identified, no filament used)
    if not active_spool_id and used == 0:
        log.info("Skipping print log — no spool tracked and no filament usage")
        active_print_name = ""
        print_start_weight = None
        return

    detail = f"{status}, {used:.1f}g used"
    if print_name:
        detail = f"{print_name} — {detail}"

    log_event(
        "print_done",
        spool_id=active_spool_id,
        spool_name=spool_name,
        detail=detail,
    )
    log_print(
        spool_id=active_spool_id,
        spool_name=spool_name,
        material=material,
        color_hex=color_hex,
        grams_used=used,
        status=status,
        print_name=print_name,
        duration=duration,
    )
    active_print_name = ""
    print_start_time = None
    print_start_weight = None


# ============================================================
# MQTT CALLBACKS
# ============================================================

def on_connect(client, userdata, flags, rc, properties=None):
    if rc == 0:
        topic = f"device/{PRINTER_SERIAL}/report"
        client.subscribe(topic)
        log.info(f"Connected to P2S at {PRINTER_IP}")
        log.info(f"Subscribed to {topic}")

        request_topic = f"device/{PRINTER_SERIAL}/request"
        client.subscribe(request_topic)
        log.info(f"Subscribed to {request_topic}")

        # Request full status dump
        client.publish(request_topic, json.dumps({"pushing": {"command": "pushall"}}))
    else:
        log.error(f"MQTT connect failed: rc={rc}")


def on_disconnect(client, userdata, rc, properties=None, reason_code=None):
    log.warning(f"Disconnected from MQTT (rc={rc}), reconnecting...")
    try:
        bridge_state_path.write_text(json.dumps({"connected": False, "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}))
    except Exception as e:
        log.warning(f"Could not write disconnect state: {e}")


def _handle_request_message(data):
    """Handle messages on the request topic (OpenSpool tag reads)."""
    fs = parse_request_filament_setting(data)
    if fs:
        log.info(f"OpenSpool tag read: {fs['type']} #{fs['color']}")
        handle_vt_tray_change(fs)


def on_message(client, userdata, msg):
    if msg.topic.endswith("/request"):
        # Skip echoes of our own publish without even decoding the payload.
        if time.time() - _last_own_publish_time < ECHO_SUPPRESS_SECONDS:
            return
        try:
            data = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        _handle_request_message(data)
        return

    try:
        data = json.loads(msg.payload)
    except json.JSONDecodeError:
        return

    # Parse external spool (virtual tray)
    vt = parse_vt_tray(data)
    if vt:
        handle_vt_tray_change(vt)

    # Parse AMS environment + tray remaining % (before tray change handler so remain is available)
    global ams_environment, ams_tray_remain, _bridge_state_dirty
    env = parse_ams_environment(data)
    if env and env != ams_environment:
        ams_environment = env
        _bridge_state_dirty = True

    remain = parse_ams_remain(data)
    if remain and remain != ams_tray_remain:
        ams_tray_remain = remain
        _bridge_state_dirty = True
        sync_remain_to_spoolman()

    # Parse AMS trays
    ams = parse_ams_trays(data)
    if ams:
        handle_ams_change(ams)

    # Parse live printer telemetry
    global printer_telemetry, print_start_time, print_start_weight, active_spool_id, active_print_name
    telem = parse_printer_telemetry(data)
    if telem:
        # Track print start time and snapshot spool weight for usage calculation
        if telem["gcode_state"] == "RUNNING" and print_start_time is None:
            print_start_time = time.time()
            # Snapshot weight so we can calculate grams used at print end
            if active_spool_id:
                try:
                    s = sm_get(f"spool/{active_spool_id}")
                    print_start_weight = s.get("remaining_weight", 0)
                    log.info(f"Print start: spool #{active_spool_id} at {print_start_weight:.0f}g")
                except Exception:
                    print_start_weight = None
        elif telem["gcode_state"] != "RUNNING":
            print_start_time = None

        if print_start_time:
            telem["print_elapsed"] = int(time.time() - print_start_time)
        else:
            telem["print_elapsed"] = 0

        if telem != printer_telemetry:
            printer_telemetry = telem
            _bridge_state_dirty = True

    # Track which spool is actively printing (AMS tray_now → spool ID)
    active = parse_active_tray(data)
    if active:
        active_print_name = active["subtask_name"] or active_print_name
        tray_now = active["tray_now"]
        if tray_now is not None and tray_now != "255":
            # AMS tray — look up which spool is in that slot
            slot_key = f"A0_{tray_now}"
            tray_data = current_ams_trays.get(slot_key)
            if tray_data:
                tag_uid = tray_data.get("tag_uid", "")
                tray_uuid = tray_data.get("tray_uuid", "")
                if is_real_tag(tag_uid) or is_real_tag(tray_uuid):
                    spool = find_spool_by_tag(tag_uid, tray_uuid)
                    if spool:
                        active_spool_id = spool["id"]

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

    # Restore print state from previous session if a print was in progress
    global print_start_time, print_start_weight, active_spool_id, active_print_name
    try:
        prev = json.loads(bridge_state_path.read_text())
        if prev.get("printer", {}).get("gcode_state") == "RUNNING":
            if prev.get("print_start_time"):
                print_start_time = prev["print_start_time"]
            if prev.get("print_start_weight") is not None:
                print_start_weight = prev["print_start_weight"]
            if prev.get("active_spool_id"):
                active_spool_id = prev["active_spool_id"]
            if prev.get("active_print_name"):
                active_print_name = prev["active_print_name"]
            log.info(f"Restored print state: spool #{active_spool_id}, \"{active_print_name}\"")
    except Exception:
        pass

    # Verify Spoolman is reachable
    try:
        info = requests.get(f"{SPOOLMAN_URL}/api/v1/info", timeout=5)
        log.info(f"Spoolman OK: v{info.json().get('version', '?')}")
    except Exception as e:
        log.error(f"Cannot reach Spoolman at {SPOOLMAN_URL}: {e}")
        sys.exit(1)

    ensure_extra_fields()

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
