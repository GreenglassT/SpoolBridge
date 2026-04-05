#!/usr/bin/env python3
"""
NFC Tag Writer Helper

Pulls spool data from Spoolman and generates OpenSpool-format JSON
for writing to NTAG215 NFC stickers.

Usage:
  ./write_tags.py              # list all spools with tag JSON
  ./write_tags.py 16           # show tag JSON for spool #16
  ./write_tags.py --all        # show all spools
  ./write_tags.py --no-bambu   # skip Bambu spools (they have built-in RFID)

The JSON output can be copy-pasted into NFC Tools app on iPhone:
  Write → Add a record → Custom → MIME type: application/json
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

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

# Material → OpenSpool type mapping
# Bambu printer expects specific type strings
MATERIAL_MAP = {
    "pla": "PLA",
    "pla basic": "PLA",
    "pla+": "PLA",
    "pla matte": "PLA",
    "pla silk": "PLA",
    "pla-cf": "PLA-CF",
    "pla uv": "PLA",
    "pla wood": "PLA",
    "petg": "PETG",
    "petg basic": "PETG",
    "petg hf": "PETG",
    "petg-hf": "PETG",
    "petg-cf": "PETG-CF",
    "abs": "ABS",
    "asa": "ASA",
    "tpu": "TPU",
    "tpu 90a": "TPU",
    "pa": "PA",
    "pa-cf": "PA-CF",
    "pa6-cf": "PA-CF",
    "pc": "PC",
    "pva": "PVA",
}

# Default temp ranges per material type
DEFAULT_TEMPS = {
    "PLA":     (190, 220),
    "PLA-CF":  (220, 240),
    "PETG":    (230, 260),
    "PETG-CF": (250, 270),
    "ABS":     (240, 270),
    "ASA":     (240, 270),
    "TPU":     (210, 230),
    "PA":      (270, 300),
    "PA-CF":   (270, 300),
    "PC":      (260, 290),
    "PVA":     (190, 210),
}


# ============================================================
# SPOOLMAN API
# ============================================================

def api_get(endpoint):
    url = f"{SPOOLMAN_URL}/api/v1/{endpoint}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def get_all_spools():
    return api_get("spool")


# ============================================================
# TAG GENERATION
# ============================================================

def spool_to_openspool_json(spool):
    """Convert a Spoolman spool record to OpenSpool NFC tag JSON."""
    f = spool.get("filament", {})
    vendor = f.get("vendor", {})

    material = (f.get("material") or "PLA").strip()
    color_hex = (f.get("color_hex") or "000000").lower()
    brand = vendor.get("name", "Generic")

    # Map material to OpenSpool type
    openspool_type = MATERIAL_MAP.get(material.lower(), material.upper())

    # Get temp range from filament settings or defaults
    nozzle_min = f.get("settings_extruder_temp")
    nozzle_max = nozzle_min
    if not nozzle_min:
        default = DEFAULT_TEMPS.get(openspool_type, (190, 220))
        nozzle_min, nozzle_max = default
    else:
        # Use +/- 10 if only one temp is set
        nozzle_min = max(nozzle_min - 10, 150)
        nozzle_max = nozzle_max + 10

    tag_data = {
        "protocol": "openspool",
        "version": "1.0",
        "type": openspool_type,
        "color_hex": color_hex.upper(),
        "brand": brand,
        "min_temp": str(nozzle_min),
        "max_temp": str(nozzle_max),
    }

    return tag_data


def print_spool_tag(spool, show_url=True):
    """Print a spool's info and OpenSpool NFC tag JSON."""
    f = spool.get("filament", {})
    vendor = f.get("vendor", {})
    remaining = spool.get("remaining_weight", 0)
    tag_data = spool_to_openspool_json(spool)

    name = f.get("name", "Unknown")
    material = f.get("material", "?")
    brand = vendor.get("name", "?")
    color = f.get("color_hex", "?")

    print(f"\n{'='*60}")
    print(f"  Spool #{spool['id']}:  {name}")
    print(f"  Brand: {brand}  |  Material: {material}  |  Color: #{color}")
    print(f"  Remaining: {remaining:.0f}g")
    print(f"{'='*60}")
    print(f"\n  OpenSpool NFC Tag JSON (copy into NFC Tools):")
    print(f"  MIME type: application/json")
    print()
    print(f"  {json.dumps(tag_data, indent=2)}")

    if show_url:
        base = os.environ.get(
            "OPENSPOOLMAN_URL", "https://openspoolman.greenglasst.com"
        )
        print(f"\n  OpenSpoolman URL (optional 2nd NDEF record):")
        print(f"  {base}/spool_info?spool_id={spool['id']}")

    print()


# ============================================================
# MAIN
# ============================================================

def main():
    args = sys.argv[1:]
    skip_bambu = "--no-bambu" in args
    show_all = "--all" in args or not args
    args = [a for a in args if not a.startswith("--")]

    spools = get_all_spools()

    if not spools:
        print("No spools found in Spoolman.")
        return

    # Specific spool ID requested
    if args and args[0].isdigit():
        spool_id = int(args[0])
        for s in spools:
            if s["id"] == spool_id:
                print_spool_tag(s)
                return
        print(f"Spool #{spool_id} not found.")
        return

    # List all spools
    if skip_bambu:
        spools = [
            s for s in spools
            if (s.get("filament", {}).get("vendor", {}).get("name", ""))
               .lower() != "bambu lab"
        ]

    if not spools:
        print("No spools to show (after filtering).")
        return

    print(f"\n{'='*60}")
    print(f"  Spoolman Inventory → OpenSpool NFC Tag Data")
    print(f"  Spoolman: {SPOOLMAN_URL}")
    print(f"  {len(spools)} spool(s)")
    print(f"{'='*60}")

    for s in sorted(spools, key=lambda x: x["id"]):
        print_spool_tag(s)

    print("\nTo write a tag with NFC Tools (iPhone):")
    print("  1. Open NFC Tools → Write → Add a record")
    print('  2. Select "Custom" → MIME type: application/json')
    print("  3. Paste the JSON above")
    print("  4. Tap Write → hold NTAG215 sticker to phone")
    print("  5. Stick on the spool")


if __name__ == "__main__":
    main()
