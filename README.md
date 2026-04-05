# SpoolBridge

Automated filament inventory tracking for the Bambu Lab P2S with AMS 2 Pro.
Combines OpenSpool, Spoolman, and a local web dashboard into one unified system
that automatically tracks which spool is in which tray and how much filament remains.

---

## Architecture

```
                    ┌───────────────┐
                    │  NFC Tag on   │
                    │    Spool      │
                    └──────┬────────┘
                           │ tap
                    ┌──────▼────────┐
                    │  OpenSpool    │
                    │  Mini (ESP32) │
                    └──────┬────────┘
                           │ MQTT
                    ┌──────▼────────┐
                    │   Bambu P2S   │◄──── AMS 2 Pro reads
                    │   Printer     │      built-in RFID tags
                    └──────┬────────┘
                           │ MQTT (TLS 8883)
              ┌────────────▼────────────┐
              │  bridge.py (daemon)     │
              │  Listens to printer,    │
              │  syncs state to Spoolman│
              └────────────┬────────────┘
                           │ REST API
              ┌────────────▼────────────┐
              │  Spoolman (database)    │
              │  localhost:7912         │
              └────────────┬────────────┘
                           │
              ┌────────────▼────────────┐
              │  server.py (Flask)      │
              │  Web dashboard + API    │
              │  localhost:5555         │
              └─────────────────────────┘
```

### Components

| Component | Role |
|-----------|------|
| **bridge.py** | MQTT daemon — listens to printer events, matches spools by RFID tag or color+material, updates Spoolman tray assignments and remaining weight |
| **server.py** | Flask web server — serves the dashboard UI and proxies Spoolman API with higher-level operations (link spool, assign tray, CRUD for filaments/vendors/spools) |
| **static/index.html** | Single-page web dashboard — manage inventory, view AMS status, write NFC tags, track prints |
| **Spoolman** | External inventory database (spools, filaments, vendors, usage history) |
| **OpenSpool Mini** | ESP32 NFC reader next to the printer — tap a third-party spool to configure the printer |

---

## What's Automatic vs. Manual

### Fully Automatic (bridge.py handles it)

- **AMS spool detection** — Insert a Bambu spool, the RFID tag is matched to Spoolman and the tray assignment is updated
- **External spool detection** — Tap a third-party spool on the OpenSpool Mini, the color+material is matched to Spoolman
- **Tray tracking** — `active_tray` and `location` are updated in Spoolman every time a spool moves
- **Weight tracking** — When a print finishes, the used filament weight is subtracted from `remaining_weight`
- **Print history** — Every completed print is logged with spool, material, grams used, and status
- **Activity feed** — Tray loads, external taps, and print events are logged for the dashboard
- **Unknown spool alerts** — Unrecognized RFID tags show up on the dashboard with a one-click "Link" button

### Manual (you do it once)

- **First-time Bambu spool** — When a new Bambu spool is inserted that the system hasn't seen, click "Link to Spoolman" on the dashboard. This creates the spool record and links the RFID tag. After that, it's automatic forever.
- **New third-party spool** — Create it in the Add Spool tab, then write an NFC tag for it (see below).
- **NFC tag writing** — Use the Write Tags tab to generate the JSON, then write it to an NTAG215 sticker with the NFC Tools iPhone app.
- **Filament/vendor management** — Add or edit filament definitions and vendors in the Filaments and Vendors tabs.

---

## Web Dashboard

The dashboard runs at `http://<server-ip>:5555` and has these tabs:

### Dashboard (home)
- KPI cards: total spools, total filament weight, total value, low stock count
- AMS 2 Pro tray strip showing what's in each slot (A1–A4) with color swatches
- AMS environment (humidity, temperature)
- Unknown spool alerts with "Link to Spoolman" buttons
- Filament usage chart (bar graph of remaining weight)
- Recent activity feed
- Low stock warnings (spools under 200g)

### Spools
- Filterable, sortable spool inventory with search, brand/material filters
- Spool cards with color swatch, name, weight progress bar
- Click to expand: edit weight, location, assign to tray, archive, delete
- CSV export

### Write Tags
- Select a spool to generate OpenSpool NFC tag JSON
- Copy the JSON and Spool Info URL for writing to NTAG215 stickers
- Filter to show only third-party (non-Bambu) spools

### Add Spool
- Single spool form: brand, material, color, price
- Batch add: paste multiple lines in `Brand, Material, Color, Hex, Price` format

### Filaments
- View, create, edit, delete filament definitions (material, color, vendor, temps)

### Vendors
- Manage spool vendors (name, empty spool weight)

### Print History
- Timeline of all completed prints with spool, material, grams used, status

### Tools
- Bridge service controls (start/stop/restart)
- Legacy AMS sync (now handled automatically by the bridge)

---

## How the Data Flows

### Loading a Bambu spool into the AMS

1. Insert spool → AMS reads RFID → printer broadcasts MQTT
2. `bridge.py` receives the MQTT message, extracts `tag_uid` and `tray_uuid`
3. Searches Spoolman for a spool with a matching tag in `spool.extra.tag`
4. **Match found:** Updates `active_tray` and `location` in Spoolman, logs the event
5. **No match:** Adds to `unknown_spools` list → appears as alert on the dashboard

### Tapping a third-party spool on OpenSpool Mini

1. Tap spool → OpenSpool Mini reads NFC → configures printer via MQTT
2. Printer broadcasts virtual tray change
3. `bridge.py` receives it, matches by color hex + material type in Spoolman
4. Updates `active_tray = "External"` in Spoolman

### Print completes

1. Printer broadcasts `gcode_state = "FINISH"` (or `"FAILED"`)
2. `bridge.py` detects the state transition, extracts grams used
3. Subtracts used weight from the active spool's `remaining_weight` in Spoolman
4. Logs to `prints.json` (print history) and `activity.json` (activity feed)

### Linking an unknown spool (one-time)

1. Dashboard shows "Unknown spool detected: PETG #000000 — Link to Spoolman"
2. Click the button → `POST /api/link-spool`
3. Server creates a filament (or finds existing match), creates a spool, stores the RFID tag UID in `spool.extra.tag`
4. Next MQTT report → bridge matches the tag → spool is now tracked automatically

---

## Setup

### Prerequisites

- Bambu Lab P2S printer on your local network
- Spoolman running (default: `http://localhost:7912`)
- Python 3 with venv
- OpenSpool Mini (for third-party spool NFC reading — optional if you only use Bambu spools in the AMS)
- NTAG215 NFC stickers (for third-party spools)

### 1. Clone and install

```bash
git clone <repo-url> ~/spoolbridge
cd ~/spoolbridge
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp bridge.env.example bridge.env
nano bridge.env
```

```
PRINTER_IP=192.168.x.x
PRINTER_SERIAL=YOUR_SERIAL_NUMBER
PRINTER_ACCESS_CODE=YOUR_LAN_ACCESS_CODE
SPOOLMAN_URL=http://localhost:7912
```

**Where to find printer credentials:**
- **IP:** P2S touchscreen → Settings → Network
- **Serial:** P2S touchscreen → Settings → About
- **Access Code:** P2S touchscreen → Settings → Network → LAN Access Code

### 3. Test

```bash
source venv/bin/activate
source bridge.env
python3 bridge.py
```

You should see:
```
12:00:00 [INFO] === SpoolBridge — MQTT Sync Daemon ===
12:00:00 [INFO] Printer:  192.168.x.x (serial: XXXXX)
12:00:00 [INFO] Spoolman: http://localhost:7912
12:00:00 [INFO] Spoolman OK: v0.23.1
12:00:00 [INFO] Connected to P2S at 192.168.x.x
```

Test the web server in another terminal:
```bash
source venv/bin/activate
source bridge.env
python3 server.py
```

Open `http://<server-ip>:5555` in a browser.

### 4. Install as system services

```bash
sudo cp openspool-bridge.service /etc/systemd/system/
sudo cp spoolbridge-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openspool-bridge spoolbridge-web
```

Check status:
```bash
sudo systemctl status openspool-bridge
sudo systemctl status spoolbridge-web
sudo journalctl -u openspool-bridge -f   # live bridge logs
```

---

## Writing NFC Tags (Third-Party Spools)

Bambu spools have built-in RFID and don't need NFC tags. Third-party spools need an NTAG215 sticker so the OpenSpool Mini can identify them.

### From the web dashboard

1. Go to the **Write Tags** tab
2. Click a spool to see its tag data
3. Copy the OpenSpool JSON and (optionally) the Spool Info URL

### From the command line

```bash
python3 write_tags.py --no-bambu   # show all non-Bambu spools
python3 write_tags.py 16           # show a specific spool
```

### Write with NFC Tools (iPhone)

**Record 1 — OpenSpool JSON (required):**
1. Open NFC Tools → Write → Add a record → Custom
2. MIME type: `application/json`
3. Paste the JSON
4. Tap Write → hold NTAG215 sticker to phone

**Record 2 — Spool Info URL (optional):**
1. Add a record → URL
2. Paste the Spool Info URL
3. Write to the same tag

Stick the tag on the spool. Done.

---

## Helper Scripts

| Script | Purpose |
|--------|---------|
| `add-spool.py` | Batch-create spools: `./add-spool.py "Inland, PLA, Black, 000000, 18.99"` or pass a CSV file |
| `write_tags.py` | Generate OpenSpool NFC tag JSON from Spoolman inventory |
| `sync-ams.sh` | Legacy — interactive AMS spool discovery. Now handled by the bridge + dashboard "Link" button |

---

## Runtime Files

These are generated at runtime and git-ignored:

| File | Purpose |
|------|---------|
| `bridge_state.json` | Current printer connection state, AMS environment, unknown spools. Written by bridge.py, read by server.py |
| `activity.json` | JSON-lines event log (tray loads, external taps, print completions). Capped at 200 entries |
| `prints.json` | JSON-lines print history (spool ID, material, grams used, status) |

---

## API Endpoints

All served by `server.py` on port 5555.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/spools` | GET | List all spools from Spoolman |
| `/api/spools` | POST | Create spool (auto-creates filament and vendor if needed) |
| `/api/spools/<id>` | PATCH | Update spool (weight, location, archived) |
| `/api/spools/<id>` | DELETE | Delete spool |
| `/api/filaments` | GET/POST | List or create filaments |
| `/api/filaments/<id>` | PATCH/DELETE | Update or delete filament |
| `/api/vendors` | GET/POST | List or create vendors |
| `/api/vendors/<id>` | PATCH/DELETE | Update or delete vendor |
| `/api/tray-status` | GET | Current AMS tray assignments (aggregated from spools) |
| `/api/printer-status` | GET | Printer connection state from bridge_state.json |
| `/api/link-spool` | POST | Link unknown RFID tag to a new spool |
| `/api/spools/<id>/assign-tray` | POST | Manually assign spool to a tray |
| `/api/tag/<id>` | GET | Generate OpenSpool NFC tag JSON for a spool |
| `/api/activity` | GET | Recent activity events (last 50) |
| `/api/prints` | GET | Print history |
| `/api/bridge-status` | GET | Check if bridge.py service is running |
| `/api/bridge/<action>` | POST | Start/stop/restart bridge service |
| `/api/config` | GET | System configuration (Spoolman URL, brands) |

---

## Troubleshooting

### Bridge won't connect to printer
- Verify the printer IP hasn't changed (check touchscreen → Settings → Network)
- Verify the LAN access code is correct
- The P2S does **not** need Developer Mode enabled
- Bambu printers allow max 4 concurrent MQTT connections — close Bambu Studio if needed

### Unknown spool keeps appearing
- Click "Link to Spoolman" on the dashboard to create the spool record
- Or check that the RFID tag stored in Spoolman matches (`spool.extra.tag`)

### OpenSpool tap doesn't update Spoolman
- Check bridge logs: `journalctl -u openspool-bridge -f`
- If "No Spoolman spool matches", create the spool first in the Add Spool tab
- If multiple spools match (same color + material), archive or rename duplicates

### NFC tag not reading on OpenSpool Mini
- Red PN532: reads best at **corners** of the reader
- Blue PN532: reads best at **center**
- Verify PN532 DIP switches are set to SPI mode (switch 1 up, switch 2 down)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Printer communication | MQTT over TLS (paho-mqtt) |
| Backend | Flask (Python) |
| Frontend | Vanilla HTML/CSS/JavaScript (single file, no build step) |
| Database | Spoolman (external, REST API) |
| State files | JSON (bridge_state.json, activity.json, prints.json) |
| Process management | systemd |
| Runtime | Python 3 venv |
