#!/usr/bin/env python3
"""
add-spool.py — Create filaments and spools in Spoolman from a list or interactively.

Usage:
  Interactive:    ./add-spool.py
  From file:      ./add-spool.py filaments.csv
  Single entry:   ./add-spool.py "Bambu, PLA Basic, Pink, ff7eb3, 22.99"
  With URL:       ./add-spool.py "Inland, PLA+, Red, , 18.99, https://microcenter.com/..."
  Multiple:       ./add-spool.py "Bambu, PLA Basic, Pink, ff7eb3" "Inland, PLA+, Red, ff0000"

CSV format:  brand, material, color_name, color_hex, price, url (last 3 optional)

Set SPOOLMAN_URL env var or edit the default below.
"""

import sys
import os
import json
import csv
import io
import urllib.request
import urllib.error
from typing import Optional

SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")

# ── Brand database ────────────────────────────────────────────────

BRANDS = {
    "bambu": {
        "name": "Bambu Lab",
        "spool_weight": 251.0,
        "net_weight": 1000.0,
        "diameter": 1.75,
    },
    "inland": {
        "name": "Inland",
        "spool_weight": 145.0,
        "net_weight": 1000.0,
        "diameter": 1.75,
    },
}

# ── Material database ─────────────────────────────────────────────

MATERIALS = {
    "PLA": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
    },
    "PLA Basic": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
        "parent": "PLA",
    },
    "PLA+": {
        "density": 1.24, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
    },
    "PLA Matte": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
        "parent": "PLA",
    },
    "PLA Silk": {
        "density": 1.24, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
        "parent": "PLA",
    },
    "PLA-CF": {
        "density": 1.20, "nozzle_min": 210, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
    },
    "PLA Aero": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
        "parent": "PLA",
    },
    "PETG": {
        "density": 1.27, "nozzle_min": 220, "nozzle_max": 250, "bed_min": 70, "bed_max": 85,
    },
    "PETG Basic": {
        "density": 1.27, "nozzle_min": 220, "nozzle_max": 250, "bed_min": 70, "bed_max": 85,
        "parent": "PETG",
    },
    "PETG HF": {
        "density": 1.27, "nozzle_min": 240, "nozzle_max": 270, "bed_min": 70, "bed_max": 85,
    },
    "PETG-HF": {
        "density": 1.27, "nozzle_min": 240, "nozzle_max": 270, "bed_min": 70, "bed_max": 85,
        "parent": "PETG HF",
    },
    "PETG-CF": {
        "density": 1.23, "nozzle_min": 230, "nozzle_max": 260, "bed_min": 70, "bed_max": 85,
    },
    "PLA Silk": {
        "density": 1.24, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
    },
    "PLA Silk+": {
        "density": 1.24, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
        "parent": "PLA Silk",
    },
    "PLA Translucent": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
    },
    "PLA Wood": {
        "density": 1.15, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
    },
    "PLA Marble": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
    },
    "PLA Galaxy": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
    },
    "PLA Aero": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
    },
    "TPU 90A": {
        "density": 1.21, "nozzle_min": 210, "nozzle_max": 230, "bed_min": 40, "bed_max": 60,
        "parent": "TPU",
    },
    "PLA UV": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
    },
    "PLA High Speed": {
        "density": 1.24, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
        "parent": "PLA",
    },
    "PLA+ High Speed": {
        "density": 1.24, "nozzle_min": 200, "nozzle_max": 230, "bed_min": 45, "bed_max": 60,
        "parent": "PLA+",
    },
    "PETG+": {
        "density": 1.27, "nozzle_min": 220, "nozzle_max": 250, "bed_min": 70, "bed_max": 85,
        "parent": "PETG",
    },
    "PLA Basic": {
        "density": 1.24, "nozzle_min": 190, "nozzle_max": 220, "bed_min": 45, "bed_max": 60,
        "parent": "PLA",
    },
    "ABS": {
        "density": 1.04, "nozzle_min": 230, "nozzle_max": 260, "bed_min": 95, "bed_max": 110,
    },
    "ABS+": {
        "density": 1.04, "nozzle_min": 230, "nozzle_max": 260, "bed_min": 95, "bed_max": 110,
        "parent": "ABS",
    },
    "ASA": {
        "density": 1.07, "nozzle_min": 235, "nozzle_max": 265, "bed_min": 95, "bed_max": 110,
    },
    "TPU": {
        "density": 1.21, "nozzle_min": 210, "nozzle_max": 230, "bed_min": 40, "bed_max": 60,
    },
    "TPU 95A": {
        "density": 1.21, "nozzle_min": 210, "nozzle_max": 230, "bed_min": 40, "bed_max": 60,
        "parent": "TPU",
    },
    "Nylon": {
        "density": 1.14, "nozzle_min": 250, "nozzle_max": 280, "bed_min": 70, "bed_max": 90,
    },
    "PA": {
        "density": 1.14, "nozzle_min": 250, "nozzle_max": 280, "bed_min": 70, "bed_max": 90,
        "parent": "Nylon",
    },
    "PA6-CF": {
        "density": 1.17, "nozzle_min": 260, "nozzle_max": 290, "bed_min": 80, "bed_max": 100,
    },
    "PA6-GF": {
        "density": 1.36, "nozzle_min": 260, "nozzle_max": 290, "bed_min": 80, "bed_max": 100,
    },
    "PC": {
        "density": 1.20, "nozzle_min": 260, "nozzle_max": 300, "bed_min": 100, "bed_max": 120,
    },
    "PVA": {
        "density": 1.23, "nozzle_min": 190, "nozzle_max": 210, "bed_min": 45, "bed_max": 60,
    },
}

# ── Common color names to hex ─────────────────────────────────────

COLORS = {
    # ── Bambu Lab PLA Basic (official hex) ──
    "jade white": "ffffff",
    "magenta": "ec008c",
    "gold": "e4bd68",
    "mistletoe green": "3f8e43",
    "red": "c12e1f",
    "purple": "5e43b7",
    "beige": "f7e6de",
    "pink": "f55a74",
    "sunflower yellow": "fec600",
    "bronze": "847d48",
    "turquoise": "00b1b7",
    "indigo purple": "482960",
    "light gray": "d1d3d5",
    "light grey": "d1d3d5",
    "hot pink": "f5547c",
    "yellow": "f4ee2a",
    "cocoa brown": "6f5034",
    "cyan": "0086d6",
    "blue grey": "5b6579",
    "blue gray": "5b6579",
    "silver": "a6a9aa",
    "orange": "ff6a13",
    "bright green": "becf00",
    "brown": "9d432c",
    "blue": "0a2989",
    "dark gray": "545454",
    "dark grey": "545454",
    "gray": "8e9089",
    "grey": "8e9089",
    "pumpkin orange": "ff9016",
    "bambu green": "00ae42",
    "maroon red": "9d2235",
    "cobalt blue": "0056b8",
    "black": "000000",

    # ── Bambu Lab PLA Matte (official hex) ──
    "ivory white": "ffffff",
    "bone white": "cbc6b8",
    "desert tan": "e8dbb7",
    "latte brown": "d3b7a7",
    "caramel": "ae835b",
    "terracotta": "b15533",
    "dark brown": "7d6556",
    "dark chocolate": "4d3324",
    "lilac purple": "ae96d4",
    "sakura pink": "e8afcf",
    "mandarin orange": "f99963",
    "lemon yellow": "f7d959",
    "plum": "950051",
    "scarlet red": "de4343",
    "dark red": "bb3d43",
    "dark green": "68724d",
    "grass green": "61c680",
    "apple green": "c2e189",
    "ice blue": "a3d8e1",
    "sky blue": "56b7e6",
    "marine blue": "0078bf",
    "dark blue": "042f56",
    "ash gray": "9b9ea0",
    "ash grey": "9b9ea0",
    "nardo gray": "757575",
    "nardo grey": "757575",
    "charcoal": "000000",

    # ── Bambu Lab PLA-CF (official hex) ──
    "burgundy red": "951e23",
    "iris purple": "69398e",
    "matcha green": "5c9748",
    "jeans blue": "6e88bc",
    "royal blue": "2842ad",
    "lava gray": "4d5054",
    "lava grey": "4d5054",

    # ── Bambu Lab PLA Translucent (official hex) ──
    "light jade": "96d8af",
    "mellow yellow": "f5dbab",
    "cherry pink": "f5b6cd",

    # ── Bambu Lab PLA Silk+ (official hex) ──
    "titan gray": "5f6367",
    "titan grey": "5f6367",
    "candy red": "d02727",
    "candy green": "018814",
    "rose gold": "ba9594",
    "baby blue": "a8c6ee",
    "mint": "96dcb9",
    "champagne": "f3cfb2",

    # ── Bambu Lab ABS (official hex) ──
    "azure": "489fdf",
    "navy blue": "0c2340",
    "tangerine yellow": "ffc72c",

    # ── Bambu Lab PETG Basic (official hex) ──
    "reflex blue": "001489",

    # ── Bambu Lab PETG-CF (official hex) ──
    "brick red": "9f332a",
    "violet purple": "583061",
    "indigo blue": "324585",
    "malachite green": "16b08e",

    # ── Bambu Lab TPU 90A (official hex) ──
    "grape jelly": "d6abff",
    "crystal blue": "7eb4e1",
    "quicksilver": "9ea2a2",
    "blaze": "f1aaa8",
    "frozen": "ffffff",

    # ── Inland PLA/PLA+ colors ──
    "dark gray green": "4a5a4a",
    "dark grey green": "4a5a4a",
    "natural red": "c44536",
    "true red": "cc0000",
    "true blue": "0033cc",
    "ocean blue": "006994",
    "alpine white": "f2f0e6",
    "cornflower blue": "6495ed",
    "neon green": "39ff14",
    "olive green": "556b2f",
    "light blue": "add8e6",
    "dark blue": "00264d",
    "bone white": "cbc6b8",
    "glow in the dark blue": "1b3f8b",
    "glow in the dark green": "2e8b57",
    "matte black": "1a1a1a",
    "matte gray": "6b6b6b",
    "matte grey": "6b6b6b",
    "matte purple": "5c3a6e",
    "matte white": "e8e8e8",
    "pastel pink": "ffd1dc",
    "pastel blue": "aec6cf",
    "pastel green": "77dd77",
    "pastel yellow": "fdfd96",
    "pastel purple": "b39eb5",
    "pastel orange": "ffb347",
    "army green": "4b5320",
    "army tan": "c9b38c",
    "army brown": "6b4226",
    "glass purple": "9370db",
    "glass blue": "4682b4",
    "glass green": "2e8b57",
    "glass red": "b22222",
    "silk gold": "d4a017",
    "silk silver": "c0c0c0",
    "silk copper": "b87333",
    "silk rose": "e8a0bf",

    # ── Generic fallbacks ──
    "white": "ffffff",
    "green": "00ff00",
    "maroon": "800000",
    "navy": "000080",
    "olive": "789d4a",
    "teal": "009fa1",
    "coral": "ff7f50",
    "salmon": "fa8072",
    "lime": "32cd32",
    "lavender": "b8acd6",
    "ivory": "fffff0",
    "natural": "f5f0e0",
    "transparent": "f5f5f5",
    "clear": "f5f5f5",
    "clay brown": "ae835b",
}

# ── Spoolman API helpers ──────────────────────────────────────────

def api_get(path: str):
    """GET from Spoolman API."""
    req = urllib.request.Request(f"{SPOOLMAN_URL}{path}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"\033[0;31mError connecting to Spoolman at {SPOOLMAN_URL}: {e}\033[0m")
        sys.exit(1)


def api_post(path: str, data: dict):
    """POST to Spoolman API."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{SPOOLMAN_URL}{path}", data=body,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def api_patch(path: str, data: dict):
    """PATCH to Spoolman API."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(f"{SPOOLMAN_URL}{path}", data=body,
                                 headers={"Content-Type": "application/json"}, method="PATCH")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


# ── Lookup / creation helpers ─────────────────────────────────────

def get_or_create_vendor(brand_key: str) -> int:
    """Find or create vendor in Spoolman. Returns vendor ID."""
    brand_info = BRANDS.get(brand_key.lower())
    vendor_name = brand_info["name"] if brand_info else brand_key

    vendors = api_get("/api/v1/vendor")
    for v in vendors:
        if v["name"].lower() == vendor_name.lower():
            return v["id"]

    # Create it
    result = api_post("/api/v1/vendor", {
        "name": vendor_name,
        "empty_spool_weight": brand_info["spool_weight"] if brand_info else 200.0,
    })
    print(f"  \033[0;32mCreated vendor '{vendor_name}' (#{result['id']})\033[0m")
    return result["id"]


def find_filament(vendor_id: int, material: str, color_hex: str, name: str = "") -> Optional[int]:
    """Find existing filament by vendor + (material + color OR name)."""
    filaments = api_get("/api/v1/filament")
    mat_lower = material.lower().strip()
    color_lower = color_hex.lower().strip()
    name_lower = name.lower().strip()

    for f in filaments:
        f_vendor = f.get("vendor")
        if not f_vendor or f_vendor["id"] != vendor_id:
            continue
        f_mat = (f.get("material") or "").lower().strip()
        f_color = (f.get("color_hex") or "").lower().strip()
        f_name = (f.get("name") or "").lower().strip()
        # Match by material + color hex
        if f_mat == mat_lower and f_color == color_lower:
            return f["id"]
        # Match by name (catches hex mismatches)
        if name_lower and f_name == name_lower:
            return f["id"]
    return None


def get_material_info(material: str) -> dict:
    """Look up material properties from database."""
    # Try exact match first
    if material in MATERIALS:
        return MATERIALS[material]
    # Case-insensitive
    for key, val in MATERIALS.items():
        if key.lower() == material.lower():
            return val
    # Try matching by base type (e.g. "PLA+ Tough" -> "PLA+")
    for key, val in MATERIALS.items():
        if material.lower().startswith(key.lower()):
            return val
    # Default to PLA
    return MATERIALS["PLA"]


def resolve_color(color_input: str) -> str:
    """Convert color name or hex to a clean hex string."""
    color_input = color_input.strip().strip("#").lower()
    # If it's already hex
    if all(c in "0123456789abcdef" for c in color_input) and len(color_input) in (6, 8):
        return color_input[:6]
    # Look up name
    if color_input in COLORS:
        return COLORS[color_input]
    # Fuzzy: check if any color name is contained
    for name, hex_val in COLORS.items():
        if name in color_input or color_input in name:
            return hex_val
    return ""


def get_spoolman_material(material: str) -> str:
    """Get the material string to use in Spoolman (what the AMS reports)."""
    mat_info = MATERIALS.get(material, {})
    parent = mat_info.get("parent")
    if parent:
        # Use the parent name for AMS matching (e.g. "PLA Basic" -> "PLA")
        # But keep specific names like "PETG HF"
        return parent
    return material


# ── Parse input ───────────────────────────────────────────────────

def parse_entry(line: str) -> dict:
    """Parse a CSV line: brand, material, color_name, color_hex, price, url"""
    reader = csv.reader(io.StringIO(line))
    parts = [p.strip() for p in next(reader)]

    if len(parts) < 3:
        print(f"\033[0;31mNeed at least: brand, material, color. Got: {line}\033[0m")
        return {}

    brand = parts[0]
    material = parts[1]
    color_name = parts[2]
    color_hex = parts[3] if len(parts) > 3 else ""
    price = parts[4] if len(parts) > 4 else ""
    url = parts[5] if len(parts) > 5 else ""

    # Resolve color hex from name if not provided
    if not color_hex:
        color_hex = resolve_color(color_name)

    color_hex = color_hex.strip().strip("#").lower()
    if len(color_hex) == 8:
        color_hex = color_hex[:6]  # Strip alpha

    return {
        "brand": brand,
        "material": material,
        "color_name": color_name,
        "color_hex": color_hex,
        "price": float(price) if price else None,
        "url": url if url else None,
    }


# ── Interactive mode ──────────────────────────────────────────────

def interactive_entry() -> list[dict]:
    """Walk through adding filaments interactively. Returns a list (multiple colors = multiple entries)."""
    print()
    print("\033[1mBrands:\033[0m", ", ".join(b["name"] for b in BRANDS.values()), "(or type a new name)")
    brand = input("  Brand: ").strip()

    print()
    print("\033[1mCommon materials:\033[0m PLA, PLA+, PLA Basic, PLA Matte, PLA Silk, PLA-CF,")
    print("  PETG, PETG HF, PETG-CF, ABS, ASA, TPU, Nylon")
    material = input("  Material: ").strip()

    print()
    print("  You can enter multiple colors separated by commas.")
    colors_input = input("  Colors (e.g. Red, Blue, Jade White): ").strip()

    # Split on commas, but be smart about multi-word color names
    color_names = [c.strip() for c in colors_input.split(",") if c.strip()]

    # Resolve each color
    entries = []
    for color_name in color_names:
        color_hex = resolve_color(color_name)
        if color_hex:
            print(f"  → {color_name} = #{color_hex}")
        else:
            color_hex = input(f"  Couldn't resolve '{color_name}'. Enter hex: ").strip()
            color_hex = color_hex.strip("#").lower()
        entries.append({"color_name": color_name, "color_hex": color_hex})

    # Price (applies to all)
    print()
    price_input = input("  Price per spool (optional, Enter to skip): ").strip()
    price = float(price_input) if price_input else None

    # Remaining weight (applies to all, or per-spool)
    remaining_input = input("  Remaining weight in grams [1000 = full spool] (applies to all): ").strip()
    remaining = int(remaining_input) if remaining_input else 1000

    # Location
    print()
    print("  Common locations: AMS 2 Pro, Storage")
    location_input = input("  Location [AMS 2 Pro]: ").strip()
    location = location_input if location_input else "AMS 2 Pro"

    return [
        {
            "brand": brand,
            "material": material,
            "color_name": e["color_name"],
            "color_hex": e["color_hex"],
            "price": price,
            "url": None,
            "remaining_g": remaining,
            "location": location,
        }
        for e in entries
    ]


# ── Main logic ────────────────────────────────────────────────────

def process_entries(entries: list[dict], remaining_default: int = 1000):
    """Show summary, confirm, then create everything in Spoolman."""
    if not entries:
        print("No entries to process.")
        return

    # Enrich with material data
    for e in entries:
        mat_info = get_material_info(e["material"])
        brand_info = BRANDS.get(e["brand"].lower(), {})
        e["density"] = mat_info["density"]
        e["nozzle_min"] = mat_info["nozzle_min"]
        e["nozzle_max"] = mat_info["nozzle_max"]
        e["bed_min"] = mat_info["bed_min"]
        e["bed_max"] = mat_info["bed_max"]
        e["spool_weight"] = brand_info.get("spool_weight", 200.0)
        e["net_weight"] = brand_info.get("net_weight", 1000.0)
        e["diameter"] = brand_info.get("diameter", 1.75)
        if "remaining_g" not in e:
            e["remaining_g"] = remaining_default

    # Show summary
    print()
    print("\033[0;36m" + "=" * 80 + "\033[0m")
    print("\033[1m  Summary — the following will be created in Spoolman:\033[0m")
    print("\033[0;36m" + "=" * 80 + "\033[0m")
    print()
    print(f"  {'#':<3} {'Brand':<12} {'Material':<12} {'Color':<15} {'Hex':<8} {'Price':<7} {'Weight':<8} {'Location':<12}")
    print(f"  {'─'*3} {'─'*12} {'─'*12} {'─'*15} {'─'*8} {'─'*7} {'─'*8} {'─'*12}")

    for i, e in enumerate(entries, 1):
        price_str = f"${e['price']:.2f}" if e.get("price") else "—"
        loc = e.get("location", "AMS 2 Pro")
        print(f"  {i:<3} {e['brand']:<12} {e['material']:<12} {e['color_name']:<15} #{e['color_hex']:<7} {price_str:<7} {e['remaining_g']:<8} {loc:<12}")

    print()
    confirm = input("  Proceed? (y/n): ").strip().lower()
    if confirm != "y":
        print("  Cancelled.")
        return

    print()

    # Create everything
    for i, e in enumerate(entries, 1):
        print(f"\033[1m[{i}/{len(entries)}] {e['brand']} {e['material']} {e['color_name']}\033[0m")

        # Get or create vendor
        vendor_id = get_or_create_vendor(e["brand"])

        # Check for existing filament
        spoolman_material = get_spoolman_material(e["material"])
        fil_name = f"{e['material']} {e['color_name']}"
        filament_id = find_filament(vendor_id, spoolman_material, e["color_hex"], name=fil_name)

        if filament_id:
            print(f"  Using existing filament #{filament_id}")
        else:
            # Create filament
            fil_data = {
                "name": fil_name,
                "vendor_id": vendor_id,
                "material": spoolman_material,
                "color_hex": e["color_hex"],
                "density": e["density"],
                "diameter": e["diameter"],
                "weight": e["net_weight"],
                "spool_weight": e["spool_weight"],
                "settings_extruder_temp": e["nozzle_max"],
                "settings_bed_temp": e["bed_max"],
            }
            if e.get("price"):
                fil_data["price"] = e["price"]

            result = api_post("/api/v1/filament", fil_data)
            filament_id = result["id"]
            print(f"  \033[0;32mCreated filament '{fil_name}' (#{filament_id})\033[0m")

        # Create spool
        location = e.get("location", "AMS 2 Pro")
        spool_data = {
            "filament_id": filament_id,
            "remaining_weight": e["remaining_g"],
            "location": location,
        }
        spool_result = api_post("/api/v1/spool", spool_data)
        spool_id = spool_result["id"]
        print(f"  \033[0;32mCreated spool #{spool_id} — {e['remaining_g']}g remaining\033[0m")

        if e.get("url"):
            print(f"  URL: {e['url']}")

        print()

    print("\033[0;36m=== Done ===\033[0m")
    print()
    print("For Bambu spools: load them in the AMS and run sync-ams.sh to link RFID tags.")
    print("For third-party spools: no RFID — usage will be tracked automatically via print jobs.")


def main():
    args = sys.argv[1:]

    if not args:
        # Interactive mode
        entries = []
        print("\033[0;36m=== Add Spools to Spoolman ===\033[0m")
        print()
        print("Add filaments by brand/material. You can list multiple colors at once.")
        print("Type 'done' when finished adding brands/materials.")

        while True:
            new_entries = interactive_entry()
            if new_entries:
                entries.extend(new_entries)
                print(f"\n  \033[0;32m{len(new_entries)} color(s) added ({len(entries)} total)\033[0m")
            print()
            again = input("  Add another brand/material? (y/n): ").strip().lower()
            if again != "y":
                break

        process_entries(entries)

    elif len(args) == 1 and os.path.isfile(args[0]):
        # File mode — read CSV
        entries = []
        with open(args[0]) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                entry = parse_entry(line)
                if entry:
                    entries.append(entry)
        process_entries(entries)

    else:
        # Command-line entries
        entries = []
        for arg in args:
            entry = parse_entry(arg)
            if entry:
                entries.append(entry)
        process_entries(entries)


if __name__ == "__main__":
    main()
