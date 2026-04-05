#!/bin/bash
# sync-ams.sh — Reads AMS trays from OpenSpoolman logs and auto-creates
# any missing spools in Spoolman, including RFID tag linking.
#
# Usage: ./sync-ams.sh
# Run on the Debian machine where Docker is running.

SPOOLMAN_URL="http://localhost:7912"
CONTAINER="openspoolman"

# Bambu spool weight and default filament weight
DEFAULT_SPOOL_WEIGHT=251.0
DEFAULT_FILAMENT_WEIGHT=1000.0

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}=== AMS Spool Sync ===${NC}"
echo ""

# Get the last complete AMS block from docker logs
# Find the last "AMS [A]" header line, then grab the tray lines that follow it
AMS_BLOCK=$(docker logs "$CONTAINER" --tail 500 2>&1 | tac | sed -n '1,/^AMS \[A\]/p' | tac)
AMS_LINES=$(echo "$AMS_BLOCK" | grep -E '^\s+- \[A[0-9]\]')

if [ -z "$AMS_LINES" ]; then
    echo -e "${RED}No AMS tray data found in logs.${NC}"
    exit 1
fi

# Get all existing spools with their tags
EXISTING_SPOOLS=$(curl -s "$SPOOLMAN_URL/api/v1/spool")

# Parse each tray line
while IFS= read -r line; do
    # Parse: - [A0] PETG HF 000000FF (018%) [[ 1A3B7CF857CC429DB78999D75C3E9821 ]]
    SLOT=$(echo "$line" | grep -oP '\[A\d\]' | tr -d '[]')
    MATERIAL=$(echo "$line" | sed -E 's/.*\[A[0-9]\]\s+//' | sed -E 's/\s+[0-9A-Fa-f]{6,8}\s+\(.*//')
    COLOR_RGBA=$(echo "$line" | grep -oP '[0-9A-Fa-f]{6,8}(?=\s+\()' )
    COLOR_HEX="${COLOR_RGBA:0:6}"
    REMAINING_PCT=$(echo "$line" | grep -oP '\d+(?=%\))')
    TAG_UID=$(echo "$line" | grep -oP '\[\[\s*\K[0-9A-Fa-f]+(?=\s*\]\])')

    TRAY_NUM=$((${SLOT:1} + 1))
    REMAINING_G=$(( REMAINING_PCT * ${DEFAULT_FILAMENT_WEIGHT%.*} / 100 ))

    echo -e "${YELLOW}Tray ${TRAY_NUM} (${SLOT}):${NC} ${MATERIAL} | Color: #${COLOR_HEX} | Remaining: ${REMAINING_PCT}% (~${REMAINING_G}g) | Tag: ${TAG_UID:0:8}..."

    # Check if this tag already exists in Spoolman
    if [ -n "$TAG_UID" ]; then
        EXISTING=$(echo "$EXISTING_SPOOLS" | python3 -c "
import sys, json
spools = json.load(sys.stdin)
tag = '$TAG_UID'
for s in spools:
    extra = s.get('extra', {})
    t = extra.get('tag', '').strip('\"')
    if t == tag:
        print(s['id'])
        break
" 2>/dev/null)

        if [ -n "$EXISTING" ]; then
            echo -e "  ${GREEN}✓ Already linked to spool #${EXISTING}${NC}"
            echo ""
            continue
        fi
    fi

    echo -e "  ${RED}✗ Not in Spoolman${NC}"

    # Find or create matching filament
    # Search for existing filament by material name
    FILAMENT_ID=$(echo "$EXISTING_SPOOLS" | python3 -c "
import sys, json, urllib.request
spools = json.load(sys.stdin)
# Check existing filaments via API
data = json.loads(urllib.request.urlopen('$SPOOLMAN_URL/api/v1/filament').read())
material = '$MATERIAL'.strip()
color = '${COLOR_HEX}'.lower()
for f in data:
    f_mat = (f.get('material') or '').strip()
    f_color = (f.get('color_hex') or '').lower()
    if f_mat.lower() == material.lower() and f_color == color:
        print(f['id'])
        break
" 2>/dev/null)

    if [ -n "$FILAMENT_ID" ]; then
        echo -e "  Found matching filament #${FILAMENT_ID}"
    else
        echo -e "  No matching filament found for ${MATERIAL} #${COLOR_HEX}"
        read -p "  Create filament '${MATERIAL}' with color #${COLOR_HEX}? (y/n): " CREATE_FIL
        if [ "$CREATE_FIL" != "y" ]; then
            echo "  Skipping."
            echo ""
            continue
        fi

        # Try to determine a good name
        read -p "  Filament name (e.g. 'PETG-HF Black', 'PLA Basic White'): " FIL_NAME
        if [ -z "$FIL_NAME" ]; then
            FIL_NAME="$MATERIAL"
        fi

        # Determine base material type for Spoolman (PLA, PETG, etc.)
        BASE_MATERIAL=$(echo "$MATERIAL" | awk '{print $1}')
        if echo "$MATERIAL" | grep -qi "HF"; then
            BASE_MATERIAL="$MATERIAL"
        fi

        RESULT=$(curl -s -X POST "$SPOOLMAN_URL/api/v1/filament" \
            -H "Content-Type: application/json" \
            -d "{
                \"name\": \"${FIL_NAME}\",
                \"material\": \"${BASE_MATERIAL}\",
                \"color_hex\": \"${COLOR_HEX}\",
                \"density\": 1.24,
                \"diameter\": 1.75,
                \"weight\": ${DEFAULT_FILAMENT_WEIGHT},
                \"spool_weight\": ${DEFAULT_SPOOL_WEIGHT},
                \"vendor_id\": 1
            }")

        FILAMENT_ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

        if [ -n "$FILAMENT_ID" ]; then
            echo -e "  ${GREEN}Created filament #${FILAMENT_ID}${NC}"
        else
            echo -e "  ${RED}Failed to create filament: ${RESULT}${NC}"
            echo ""
            continue
        fi
    fi

    # Create the spool
    read -p "  Create spool with ${REMAINING_G}g remaining? (y/n/custom weight): " CREATE_SPOOL
    if [ "$CREATE_SPOOL" = "n" ]; then
        echo "  Skipping."
        echo ""
        continue
    elif [ "$CREATE_SPOOL" != "y" ]; then
        # User entered a custom weight
        REMAINING_G="$CREATE_SPOOL"
    fi

    USED_G=$(( ${DEFAULT_FILAMENT_WEIGHT%.*} - REMAINING_G ))

    SPOOL_RESULT=$(curl -s -X POST "$SPOOLMAN_URL/api/v1/spool" \
        -H "Content-Type: application/json" \
        -d "{
            \"filament_id\": ${FILAMENT_ID},
            \"remaining_weight\": ${REMAINING_G},
            \"location\": \"AMS 2 Pro\"
        }")

    SPOOL_ID=$(echo "$SPOOL_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])" 2>/dev/null)

    if [ -z "$SPOOL_ID" ]; then
        echo -e "  ${RED}Failed to create spool: ${SPOOL_RESULT}${NC}"
        echo ""
        continue
    fi

    echo -e "  ${GREEN}Created spool #${SPOOL_ID}${NC}"

    # Patch the RFID tag
    if [ -n "$TAG_UID" ]; then
        TAG_RESULT=$(curl -s -X PATCH "$SPOOLMAN_URL/api/v1/spool/${SPOOL_ID}" \
            -H "Content-Type: application/json" \
            -d "{\"extra\":{\"tag\":\"\\\"${TAG_UID}\\\"\"}}")

        TAG_CHECK=$(echo "$TAG_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('extra',{}).get('tag',''))" 2>/dev/null)

        if [ -n "$TAG_CHECK" ]; then
            echo -e "  ${GREEN}✓ RFID tag linked${NC}"
        else
            echo -e "  ${RED}Failed to patch tag: ${TAG_RESULT}${NC}"
        fi
    fi

    echo ""

done <<< "$AMS_LINES"

echo -e "${CYAN}=== Done ===${NC}"
