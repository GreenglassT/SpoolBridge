#!/bin/bash
# add-spool.sh — Interactively create filaments and spools in Spoolman
# Works for any brand — Bambu, Inland, eSUN, Hatchbox, etc.
#
# Usage: ./add-spool.sh

SPOOLMAN_URL="http://localhost:7912"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

echo -e "${CYAN}=== Add Spool to Spoolman ===${NC}"
echo ""

# ── Step 1: Pick or create a filament ──────────────────────────────

echo -e "${BOLD}Step 1: Select filament${NC}"
echo ""

# Fetch existing filaments
FILAMENTS_JSON=$(curl -s "$SPOOLMAN_URL/api/v1/filament")
FILAMENT_COUNT=$(echo "$FILAMENTS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)

if [ "$FILAMENT_COUNT" -gt 0 ] 2>/dev/null; then
    echo "  Existing filaments:"
    echo "$FILAMENTS_JSON" | python3 -c "
import sys, json
filaments = json.load(sys.stdin)
for f in filaments:
    vendor = f.get('vendor', {})
    vname = vendor.get('name', '?') if vendor else '?'
    print(f'    [{f[\"id\"]}] {f[\"name\"]} ({f.get(\"material\",\"?\")} - {vname}) #{f.get(\"color_hex\",\"???\")}')
"
    echo ""
    echo "  Enter a filament ID to use it, or 'n' to create a new one."
    read -p "  Choice: " FIL_CHOICE
else
    echo "  No filaments found."
    FIL_CHOICE="n"
fi

if [ "$FIL_CHOICE" = "n" ] || [ "$FIL_CHOICE" = "N" ]; then
    echo ""
    echo -e "${BOLD}  Create new filament:${NC}"
    echo ""

    # Vendor
    VENDORS_JSON=$(curl -s "$SPOOLMAN_URL/api/v1/vendor")
    VENDOR_COUNT=$(echo "$VENDORS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null)

    if [ "$VENDOR_COUNT" -gt 0 ] 2>/dev/null; then
        echo "  Existing vendors:"
        echo "$VENDORS_JSON" | python3 -c "
import sys, json
for v in json.load(sys.stdin):
    print(f'    [{v[\"id\"]}] {v[\"name\"]}')
"
    fi
    echo ""
    echo "  Enter a vendor ID, or type a new vendor name."
    read -p "  Vendor: " VENDOR_INPUT

    # Check if it's a number (existing vendor) or name (new vendor)
    if [[ "$VENDOR_INPUT" =~ ^[0-9]+$ ]]; then
        VENDOR_ID="$VENDOR_INPUT"
    else
        # Create new vendor
        VENDOR_RESULT=$(curl -s -X POST "$SPOOLMAN_URL/api/v1/vendor" \
            -H "Content-Type: application/json" \
            -d "{\"name\": \"${VENDOR_INPUT}\"}")
        VENDOR_ID=$(echo "$VENDOR_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)
        if [ -n "$VENDOR_ID" ]; then
            echo -e "  ${GREEN}Created vendor '${VENDOR_INPUT}' (#${VENDOR_ID})${NC}"
        else
            echo -e "  ${RED}Failed to create vendor: ${VENDOR_RESULT}${NC}"
            exit 1
        fi
    fi

    # Material
    echo ""
    echo "  Common materials: PLA, PLA+, PETG, PETG HF, ABS, ASA, TPU, PETG-CF, PLA-CF"
    read -p "  Material: " MATERIAL

    # Name
    read -p "  Filament name (e.g. 'PLA+ Red', 'PETG-HF Black'): " FIL_NAME

    # Color
    read -p "  Color hex (e.g. ff0000 for red, 000000 for black): " COLOR_HEX
    COLOR_HEX=$(echo "$COLOR_HEX" | tr -d '#')

    # Weight
    read -p "  Net filament weight per spool in grams [1000]: " FIL_WEIGHT
    FIL_WEIGHT=${FIL_WEIGHT:-1000}

    # Empty spool weight
    read -p "  Empty spool weight in grams [251]: " SPOOL_WEIGHT
    SPOOL_WEIGHT=${SPOOL_WEIGHT:-251}

    # Diameter
    read -p "  Diameter in mm [1.75]: " DIAMETER
    DIAMETER=${DIAMETER:-1.75}

    # Density (guess from material)
    case $(echo "$MATERIAL" | tr '[:lower:]' '[:upper:]') in
        PLA*) DEFAULT_DENSITY=1.24 ;;
        PETG*) DEFAULT_DENSITY=1.27 ;;
        ABS*) DEFAULT_DENSITY=1.04 ;;
        ASA*) DEFAULT_DENSITY=1.07 ;;
        TPU*) DEFAULT_DENSITY=1.21 ;;
        *) DEFAULT_DENSITY=1.24 ;;
    esac
    read -p "  Density [${DEFAULT_DENSITY}]: " DENSITY
    DENSITY=${DENSITY:-$DEFAULT_DENSITY}

    # Price
    read -p "  Price (optional, e.g. 22.99): " PRICE

    # Build JSON
    FIL_JSON="{\"name\":\"${FIL_NAME}\",\"material\":\"${MATERIAL}\",\"color_hex\":\"${COLOR_HEX}\",\"density\":${DENSITY},\"diameter\":${DIAMETER},\"weight\":${FIL_WEIGHT},\"spool_weight\":${SPOOL_WEIGHT},\"vendor_id\":${VENDOR_ID}"
    if [ -n "$PRICE" ]; then
        FIL_JSON="${FIL_JSON},\"price\":${PRICE}"
    fi
    FIL_JSON="${FIL_JSON}}"

    RESULT=$(curl -s -X POST "$SPOOLMAN_URL/api/v1/filament" \
        -H "Content-Type: application/json" \
        -d "$FIL_JSON")

    FILAMENT_ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

    if [ -n "$FILAMENT_ID" ]; then
        echo -e "  ${GREEN}Created filament '${FIL_NAME}' (#${FILAMENT_ID})${NC}"
    else
        echo -e "  ${RED}Failed to create filament: ${RESULT}${NC}"
        exit 1
    fi
else
    FILAMENT_ID="$FIL_CHOICE"
    FIL_NAME=$(echo "$FILAMENTS_JSON" | python3 -c "
import sys, json
fid = int('$FILAMENT_ID')
for f in json.load(sys.stdin):
    if f['id'] == fid:
        print(f['name'])
        break
" 2>/dev/null)
    echo -e "  Using: ${FIL_NAME} (#${FILAMENT_ID})"
fi

# ── Step 2: Create the spool ──────────────────────────────────────

echo ""
echo -e "${BOLD}Step 2: Create spool${NC}"
echo ""

read -p "  Remaining weight in grams (or % like '85%' for 85% of full) [1000]: " WEIGHT_INPUT
WEIGHT_INPUT=${WEIGHT_INPUT:-1000}

# Handle percentage input
if [[ "$WEIGHT_INPUT" == *% ]]; then
    PCT=${WEIGHT_INPUT//%/}
    # Get the filament weight
    FIL_WEIGHT_G=$(echo "$FILAMENTS_JSON" | python3 -c "
import sys, json
fid = int('$FILAMENT_ID')
for f in json.load(sys.stdin):
    if f['id'] == fid:
        print(int(f.get('weight', 1000)))
        break
" 2>/dev/null)
    FIL_WEIGHT_G=${FIL_WEIGHT_G:-1000}
    REMAINING_G=$(( PCT * FIL_WEIGHT_G / 100 ))
    echo "  ${PCT}% of ${FIL_WEIGHT_G}g = ${REMAINING_G}g"
else
    REMAINING_G="$WEIGHT_INPUT"
fi

# Location
read -p "  Location [AMS 2 Pro]: " LOCATION
LOCATION=${LOCATION:-AMS 2 Pro}

SPOOL_RESULT=$(curl -s -X POST "$SPOOLMAN_URL/api/v1/spool" \
    -H "Content-Type: application/json" \
    -d "{
        \"filament_id\": ${FILAMENT_ID},
        \"remaining_weight\": ${REMAINING_G},
        \"location\": \"${LOCATION}\"
    }")

SPOOL_ID=$(echo "$SPOOL_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

if [ -z "$SPOOL_ID" ]; then
    echo -e "  ${RED}Failed to create spool: ${SPOOL_RESULT}${NC}"
    exit 1
fi

echo -e "  ${GREEN}Created spool #${SPOOL_ID} with ${REMAINING_G}g remaining${NC}"

# ── Step 3: RFID tag (optional) ───────────────────────────────────

echo ""
read -p "  Link RFID tag? Paste tag UID or 'skip' to do it later via sync-ams.sh: " TAG_INPUT

if [ "$TAG_INPUT" != "skip" ] && [ -n "$TAG_INPUT" ]; then
    TAG_RESULT=$(curl -s -X PATCH "$SPOOLMAN_URL/api/v1/spool/${SPOOL_ID}" \
        -H "Content-Type: application/json" \
        -d "{\"extra\":{\"tag\":\"\\\"${TAG_INPUT}\\\"\"}}")

    TAG_CHECK=$(echo "$TAG_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('extra',{}).get('tag',''))" 2>/dev/null)

    if [ -n "$TAG_CHECK" ]; then
        echo -e "  ${GREEN}RFID tag linked${NC}"
    else
        echo -e "  ${RED}Failed to patch tag: ${TAG_RESULT}${NC}"
    fi
else
    echo "  No tag — run sync-ams.sh after loading it in the AMS to link automatically."
fi

echo ""
echo -e "${CYAN}=== Done ===${NC}"
