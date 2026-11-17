# SpoolBridge

Automated filament inventory tracking for the Bambu Lab P2S with AMS 2 Pro.
Connects your printer's MQTT telemetry to Spoolman via a local web dashboard —
automatically tracks which spool is in which tray, monitors prints in real time,
and manages your filament inventory.

Runs entirely on your local network: the bridge talks to the printer over its local MQTT port.

[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Ko-fi](https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/greenglasst)

---

## Architecture

```
                    ┌───────────────┐
                    │  NFC Tag on   │
                    │    Spool      │
                    └──────┬────────┘
                           │ tap (optional)
                    ┌──────▼────────┐
                    │  OpenSpool    │
                    │  Mini (ESP32) │  ◄── optional, for third-party spools
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
| **bridge.py** | MQTT daemon — subscribes to printer telemetry, matches spools by RFID/color, updates Spoolman, tracks print progress, pushes filament settings to printer |
| **server.py** | Flask web server — dashboard UI, Spoolman API proxy, settings management, command IPC with bridge |
| **static/index.html** | Single-page dashboard — live print status, AMS management, inventory, NFC tags, print history, settings |
| **config.py** | Shared constants, helpers, and env loader used by bridge.py, server.py, and write_tags.py |
| **Spoolman** | External inventory database (spools, filaments, vendors) |

---

## Features

### Live Print Dashboard
- Real-time print progress bar with layer count and ETA
- Nozzle and bed temperatures (current vs target)
- Fan speeds (part fan, aux fan)
- Speed profile (Silent/Normal/Sport/Ludicrous)
- Active feeding tray indicator
- Elapsed time tracking (persists across bridge restarts)

### AMS Intelligence
- Per-tray filament remaining % from printer RFID sensors
- Automatic spool detection via RFID tag matching
- Unknown spool alerts with one-click linking
- Third-party spool support — assign in the UI, filament settings are pushed to the printer via MQTT
- AMS environment monitoring (humidity, temperature)

### Inventory Management
- Full spool CRUD with color swatches, brand logos (Bambu Lab, Inland)
- Add new spools or re-buy from existing filaments with one tap
- Filament and vendor management
- Low stock warnings (spools under 200g)
- Filament usage chart with tray labels
- CSV export

### Print History & Analytics
- Print job name, duration, spool, material, grams used, pass/fail
- Analytics: total prints, success rate, filament consumed, estimated cost
- Activity feed with tray loads, NFC taps, print completions

### NFC Tag Writing
- Generate OpenSpool-compatible JSON for third-party spools
- Spool info page at `/spool/<id>` — mobile-friendly, linkable from NFC URL record
- Write tags with NFC Tools app (iPhone)

### Settings
- Configure printer IP, serial, access code, and Spoolman URLs from the web UI
- No SSH required — first-time setup through the browser

### PWA
- Installable as a home screen app on iOS and Android
- Dark and light theme support

---

## What's Automatic vs. Manual

### Fully Automatic (bridge.py handles it)

- **AMS spool detection** — Insert a Bambu spool → RFID matched → tray assignment updated in Spoolman
- **Tray tracking** — `active_tray` and `location` updated on every spool change
- **Weight tracking** — Print finishes → used grams subtracted from `remaining_weight`
- **Print history** — Job name, spool, duration, grams, status logged automatically
- **Activity feed** — Tray loads, NFC taps, prints logged in real time
- **Unknown spool alerts** — Unrecognized RFID tags appear on dashboard
- **AMS remaining %** — Per-tray percentage from printer sensors

### Manual (you do it once)

- **First-time Bambu spool** — Click "Link to Spoolman" on the dashboard alert. After that, it's automatic forever.
- **New third-party spool** — Create in Add Spool tab. Optionally write an NFC tag.
- **Third-party spool in AMS** — Assign tray in the UI → filament settings pushed to printer automatically.
- **NFC tag writing** — Write Tags tab generates JSON, use NFC Tools to write to NTAG215 sticker.

---

## Setup

### Prerequisites

- Bambu Lab P2S printer on your local network
- Spoolman running (default: `http://localhost:7912`)
- Python 3 with venv

Optional:
- OpenSpool Mini (ESP32 NFC reader for third-party spool auto-detection)
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

**Option A: Web UI (recommended)**

Start the server, open `http://<server-ip>:5555`, go to the **Settings** tab, and fill in your printer details.

**Option B: Config file**

```bash
cp bridge.env.example bridge.env
nano bridge.env
```

```
PRINTER_IP=192.168.x.x
PRINTER_SERIAL=YOUR_SERIAL_NUMBER
PRINTER_ACCESS_CODE=YOUR_LAN_ACCESS_CODE
SPOOLMAN_URL=http://localhost:7912
SPOOLMAN_PUBLIC_URL=              # optional: https://spoolman.yourdomain.com
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
12:00:00 [INFO] Spoolman OK: v0.23.1
12:00:00 [INFO] Connected to P2S at 192.168.x.x
12:00:00 [INFO] AMS baseline: 4 trays loaded (no events logged)
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
sudo journalctl -u openspool-bridge -f   # live bridge logs
```

---

## How the Data Flows

### Loading a Bambu spool into the AMS

1. Insert spool → AMS reads RFID → printer broadcasts via MQTT
2. `bridge.py` extracts `tag_uid` and `tray_uuid`
3. Searches Spoolman for a matching spool in `spool.extra.tag`
4. **Match found:** Updates `active_tray` and `location`, logs the event
5. **No match:** Added to `unknown_spools` → alert on dashboard

### Assigning a third-party spool to an AMS tray

1. Physically load the spool into the AMS
2. In SpoolBridge, assign the spool to the tray (e.g. A1)
3. Server updates Spoolman AND writes a command for bridge.py
4. Bridge publishes `ams_filament_setting` via MQTT with material, temps, and color
5. Printer now recognizes the filament type for that tray

### Print completes

1. Printer broadcasts `gcode_state = "FINISH"` (or `"FAILED"`)
2. `bridge.py` detects the state transition, captures job name and duration
3. Subtracts used weight from the active spool's `remaining_weight`
4. Logs to `prints.json` (history) and `activity.json` (feed)

### Linking an unknown spool (one-time)

1. Dashboard shows "Unknown spool detected" alert
2. Click "Link to Spoolman" → creates spool record with RFID tag UID
3. Next MQTT report → bridge matches the tag → spool tracked automatically

---

## Writing NFC Tags (Third-Party Spools)

Bambu spools have built-in RFID — no NFC tags needed. Third-party spools can optionally get NTAG215 stickers for OpenSpool Mini auto-detection.

### From the web dashboard

1. Go to **Write Tags** tab
2. Click a spool to see its tag data
3. Copy the OpenSpool JSON and Spool Info URL

### Write with NFC Tools (iPhone)

**Record 1 — OpenSpool JSON (required):**
1. Open NFC Tools → Write → Add a record → Custom
2. MIME type: `application/json`
3. Paste the JSON → Write → hold NTAG215 sticker to phone

**Record 2 — Spool Info URL (optional):**
1. Add a record → URL
2. Paste the Spool Info URL → write to same tag

### iOS Shortcut (Load External via NFC)

See [docs/ios-shortcut.md](docs/ios-shortcut.md) for setting up an iPhone Shortcut that reads a spool's NFC tag and sends it to SpoolBridge to load as the external filament.

---

## Runtime Files

Generated at runtime, git-ignored:

| File | Purpose |
|------|---------|
| `bridge_state.json` | Printer state, AMS environment, telemetry, print progress. Written by bridge.py, read by server.py. Persists `print_start_time`, `active_spool_id`, and `active_print_name` across restarts. |
| `activity.json` | JSON-lines event log (tray loads, taps, prints). Capped at 200 entries. |
| `prints.json` | JSON-lines print history (job name, spool, material, grams, duration, status). |
| `bridge_commands.json` | IPC: server.py writes commands, bridge.py consumes them via atomic rename. |

---

## API Endpoints

All served by `server.py` on port 5555.

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/spools` | GET | List all spools |
| `/api/spools` | POST | Create spool (auto-creates filament/vendor if needed) |
| `/api/spools/<id>` | PATCH | Update spool (weight, location, archived) |
| `/api/spools/<id>` | DELETE | Delete spool |
| `/api/spools/from-filament/<id>` | POST | Create new spool from existing filament |
| `/api/spools/<id>/assign-tray` | POST | Assign spool to tray (also pushes to printer for AMS trays) |
| `/api/load-external/<id>` | POST | Load spool to external tray via MQTT |
| `/api/filaments` | GET/POST | List or create filaments |
| `/api/filaments/<id>` | PATCH/DELETE | Update or delete filament |
| `/api/vendors` | GET/POST | List or create vendors |
| `/api/vendors/<id>` | PATCH/DELETE | Update or delete vendor |
| `/api/tray-status` | GET | Current AMS tray assignments |
| `/api/printer-status` | GET | Printer state, telemetry, AMS remaining % |
| `/api/link-spool` | POST | Link unknown RFID tag to a new spool |
| `/api/tag/<id>` | GET | Generate OpenSpool NFC tag JSON |
| `/api/activity` | GET/DELETE | Recent activity events / clear |
| `/api/prints` | GET | Print history |
| `/api/settings` | GET/POST | Read/write configuration (bridge.env) |
| `/api/bridge-status` | GET | Check if bridge service is running |
| `/api/bridge/<action>` | POST | Start/stop/restart bridge service |
| `/api/config` | GET | Public config (Spoolman URL, brands) |
| `/spool/<id>` | GET | Spool info page (mobile-friendly) |

---

## Troubleshooting

### Bridge won't connect to printer
- Verify the printer IP hasn't changed (touchscreen → Settings → Network)
- Verify the LAN access code is correct
- The P2S does **not** need Developer Mode enabled
- Bambu printers allow max 4 concurrent MQTT connections — close Bambu Studio if needed

### Unknown spool keeps appearing
- Click "Link to Spoolman" on the dashboard to create the spool record
- Or check that the RFID tag stored in Spoolman matches (`spool.extra.tag`)

### Third-party spool settings not pushing to printer
- Check bridge logs: `journalctl -u openspool-bridge -f`
- The bridge must be running to process commands from the server
- Verify the spool has material and temp settings in the filament definition

### NFC tag not reading on OpenSpool Mini
- Red PN532: reads best at **corners** of the reader
- Blue PN532: reads best at **center**
- Verify DIP switches are set to SPI mode (switch 1 up, switch 2 down)

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Printer communication | MQTT over TLS (paho-mqtt) |
| Backend | Flask (Python) |
| Frontend | Vanilla HTML/CSS/JavaScript (single file, no build step) |
| Database | Spoolman (external, REST API) |
| State/IPC | JSON files (bridge_state, activity, prints, commands) |
| Process management | systemd |
| Runtime | Python 3 venv |

---

## Support

SpoolBridge is free and MIT licensed. If it saves you from weighing spools by hand,
you can [buy me a coffee on Ko-fi](https://ko-fi.com/greenglasst). Bug reports and
pull requests are welcome too.

## License

MIT. See [LICENSE](LICENSE).
