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

from config import load_env, build_openspool_tag

# ============================================================
# CONFIGURATION
# ============================================================

load_env()

SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")


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

def print_spool_tag(spool, show_url=True):
    """Print a spool's info and OpenSpool NFC tag JSON."""
    f = spool.get("filament", {})
    vendor = f.get("vendor", {})
    remaining = spool.get("remaining_weight", 0)
    tag_data = build_openspool_tag(spool)

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
        base = os.environ.get("SPOOLBRIDGE_URL", "http://localhost:5555")
        print(f"\n  Spool Info URL (optional 2nd NDEF record):")
        print(f"  {base}/spool/{spool['id']}")

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
