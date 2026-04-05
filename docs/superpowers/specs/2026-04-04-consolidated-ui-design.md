# SpoolBridge Consolidated UI

**Goal:** Replace Spoolman and OpenSpoolman as the day-to-day interface. Everything lives at `http://192.168.50.80:5555`.

**Backends:** Spoolman (inventory database at `:7912`) and OpenSpoolman (print tracking at `:8001`) continue running as data stores. SpoolBridge becomes the only UI the user opens.

---

## Dashboard Tab (redesigned)

Replaces the current KPI row. Three sections:

### AMS Tray View
- 2x2 grid matching the physical AMS 2 Pro layout
- Each occupied tray shows: color swatch (circle), filament name, brand, weight in grams, weight bar (green >30%, yellow >10%, red <=10%)
- Empty trays show dashed border with "Empty" label and tray number
- External spool badge to the right of the AMS grid, orange border, same spool info
- "AMS 2 Pro" label above the grid, "External" label above the badge

### Activity Feed (left column below AMS)
- Scrollable list of recent bridge events, newest first
- Event types with colored dot indicators:
  - Green dot: print finished (spool name, grams used)
  - Blue dot: AMS tray loaded (tray number, spool name)
  - Orange dot: external spool tap (spool name)
- Timestamps in HH:MM format
- Shows last ~20 events

### Low Stock Alerts (right column below AMS)
- Spools with remaining_weight < 200g
- Each shows: color swatch, filament name, remaining grams
- Red border for <100g, yellow border for 100-200g
- If no low stock spools, shows "All stocked" message

### Polling
- Dashboard auto-refreshes every 5 seconds
- Fetches `/api/tray-status` and `/api/activity`

---

## Spools Tab (enhanced)

Existing spool grid with search and brand/material filters — unchanged.

### Spool Detail (new)
- Clicking a spool card expands it inline (or opens a detail panel)
- Shows all fields: ID, filament name, material, color hex, brand/vendor, remaining weight, total weight, location, active tray, RFID tag UID, price
- Weight bar with percentage

### Edit Spool (new)
- Edit button on detail view
- Editable fields: remaining_weight (manual weigh-in), location (freeform text)
- Saves via `PATCH /api/spools/<id>`

### Delete Spool (new)
- Delete button on detail view with confirmation prompt
- Calls `DELETE /api/spools/<id>`

---

## Print History Tab (new)

Replaces the need to visit OpenSpoolman's print history page.

- Scrollable list of completed prints, newest first
- Each entry shows: timestamp (date + time), spool color swatch + name, material type, grams used, status (finished/failed)
- Data source: `prints.json` file written by bridge.py

---

## Tabs Unchanged

- **Write Tags** — stays as-is
- **Add Spool** — stays as-is
- **Tools** — stays as-is (Sync AMS button, bridge status, logs)

---

## Backend Changes

### bridge.py

**Activity log (`activity.json`):**
- Append a JSON line on each event: tray change, external tap, print completion
- Each entry: `{"ts": "ISO8601", "type": "tray_load|external_tap|print_finish|print_fail", "tray": "A1", "spool_id": 16, "spool_name": "PLA Black", "detail": "42g used"}`
- Cap at 200 entries (truncate oldest on write)

**Print log (`prints.json`):**
- Append a JSON line on each print completion
- Each entry: `{"ts": "ISO8601", "spool_id": 16, "spool_name": "PLA Black", "material": "PLA", "color_hex": "000000", "grams_used": 42.5, "status": "finished"}`
- No cap (grows over time, one entry per print is minimal)

### server.py

**New endpoints:**
- `GET /api/tray-status` — fetches all spools from Spoolman, filters to those with `extra.active_tray` set, returns grouped by tray name (A1-A4, External). Also returns empty tray slots.
- `GET /api/activity` — reads `activity.json`, returns last 50 entries newest-first
- `GET /api/prints` — reads `prints.json`, returns all entries newest-first
- `PATCH /api/spools/<id>` — proxies to Spoolman `PATCH /api/v1/spool/<id>` with provided fields (remaining_weight, location)
- `DELETE /api/spools/<id>` — proxies to Spoolman `DELETE /api/v1/spool/<id>`

---

## Data Flow

```
Printer (MQTT) → bridge.py → Spoolman API (tray assignments, weight updates)
                           → activity.json (event log)
                           → prints.json (print history)

Browser → server.py → Spoolman API (spool CRUD, tray status)
                    → activity.json (activity feed)
                    → prints.json (print history)
```

---

## What We're NOT Building

- No websockets/SSE — polling at 5s interval is sufficient
- No spool detail pages with full history timeline — just the key fields
- No drag-and-drop tray reassignment — bridge handles this automatically
- No analytics/cost tracking — keep it simple
- No user authentication — local network only
