"""
Shared configuration and constants for SpoolBridge.

Used by bridge.py, server.py, and write_tags.py.
"""

import os
from pathlib import Path

# ============================================================
# ENVIRONMENT
# ============================================================

def load_env():
    """Load bridge.env from script directory if it exists."""
    env_path = Path(__file__).parent / "bridge.env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                os.environ.setdefault(key.strip(), val.strip().strip('"'))


# ============================================================
# TRAY & LOCATION CONSTANTS
# ============================================================

VALID_TRAYS = ("A1", "A2", "A3", "A4", "External")
TRAY_EXTERNAL = "External"
LOCATION_AMS = "AMS 2 Pro"
LOCATION_EXTERNAL = "External"
LOCATION_STORAGE = "Storage"


def extra_quote(value):
    """Wrap a value in quotes for Spoolman's extra field storage."""
    return f'"{value}"'


def extra_unquote(value):
    """Strip the wrapper quotes from a Spoolman extra field value."""
    return (value or "").strip('"').strip()


def tray_location(tray):
    """Map a tray name to its Spoolman location string."""
    if tray and tray.startswith("A"):
        return LOCATION_AMS
    if tray == TRAY_EXTERNAL:
        return LOCATION_EXTERNAL
    return LOCATION_STORAGE


# ============================================================
# MATERIAL MAPPING
# ============================================================

# Spoolman material name → Bambu/OpenSpool type string
MATERIAL_MAP = {
    "pla": "PLA", "pla basic": "PLA", "pla+": "PLA", "pla matte": "PLA",
    "pla silk": "PLA", "pla-cf": "PLA-CF", "pla uv": "PLA", "pla wood": "PLA",
    "petg": "PETG", "petg basic": "PETG", "petg hf": "PETG", "petg-hf": "PETG",
    "petg-cf": "PETG-CF", "abs": "ABS", "asa": "ASA", "tpu": "TPU",
    "tpu 90a": "TPU", "pa": "PA", "pa-cf": "PA-CF", "pa6-cf": "PA-CF",
    "pc": "PC", "pva": "PVA",
}

# Default nozzle temp ranges per material type
DEFAULT_TEMPS = {
    "PLA": (190, 220), "PLA-CF": (220, 240), "PETG": (230, 260),
    "PETG-CF": (250, 270), "ABS": (240, 270), "ASA": (240, 270),
    "TPU": (210, 230), "PA": (270, 300), "PA-CF": (270, 300),
    "PC": (260, 290), "PVA": (190, 210),
}

# Bambu filament profile codes for tray_info_idx
FILAMENT_CODES = {
    "PLA": "GFL99", "PLA-CF": "GFL98", "PETG": "GFG99",
    "PETG-CF": "GFG99", "ABS": "GFB99", "ASA": "GFB98",
    "TPU": "GFU99", "PA": "GFN99", "PA-CF": "GFN98",
    "PC": "GFC99", "PVA": "GFS99",
}


def get_openspool_type(material):
    """Convert Spoolman material name to OpenSpool/Bambu type string."""
    return MATERIAL_MAP.get(material.lower().strip(), material.upper())


def get_filament_code(tray_type):
    """Get Bambu filament profile code for a tray_type."""
    return FILAMENT_CODES.get(tray_type, "GFL99")


def build_openspool_tag(spool):
    """Build OpenSpool NFC tag JSON from a Spoolman spool record."""
    f = spool.get("filament", {})
    vendor = f.get("vendor", {})

    material = (f.get("material") or "PLA").strip()
    color_hex = (f.get("color_hex") or "000000").lower()
    brand = vendor.get("name", "Generic")
    openspool_type = get_openspool_type(material)

    nozzle_min = f.get("settings_extruder_temp")
    if nozzle_min:
        nozzle_max = nozzle_min + 10
        nozzle_min = max(nozzle_min - 10, 150)
    else:
        nozzle_min, nozzle_max = DEFAULT_TEMPS.get(openspool_type, (190, 220))

    return {
        "protocol": "openspool",
        "version": "1.0",
        "type": openspool_type,
        "color_hex": color_hex.upper(),
        "brand": brand,
        "min_temp": str(nozzle_min),
        "max_temp": str(nozzle_max),
    }
