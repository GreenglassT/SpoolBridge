# SpoolBridge

Automated filament inventory tracking for the Bambu Lab P2S with AMS 2 Pro,
combining OpenSpool, Spoolman, and OpenSpoolman into one unified workflow.

---

## System Overview

```
                        ┌──────────────┐
                        │  NFC Tag on  │
                        │    Spool     │
                        └──────┬───────┘
                               │ tap
                        ┌──────▼───────┐
                        │  OpenSpool   │
                        │  Mini (ESP32)│
                        └──────┬───────┘
                               │ MQTT
                        ┌──────▼───────┐
                        │   Bambu P2S  │◄──── AMS 2 Pro reads
                        │   Printer    │      Bambu RFID tags
                        └──────┬───────┘
                               │ MQTT
                        ┌──────▼───────┐
                        │  bridge.py   │  (runs on Debian server)
                        └──────┬───────┘
                               │ REST API
                        ┌──────▼───────┐
                        │   Spoolman   │  (inventory database)
                        └──────┬───────┘
                               │
                        ┌──────▼───────┐
                        │ OpenSpoolman │  (web dashboard)
                        └──────────────┘
```

### What Each Piece Does

| Component | Role | Location |
|-----------|------|----------|
| **Spoolman** | Inventory database — spools, weight, usage history | `192.168.50.80:7912` |
| **OpenSpoolman** | Web dashboard — AMS status, spool info, tray assignment | `openspoolman.greenglasst.com` |
| **OpenSpool Mini** | NFC reader — tap spool to configure printer | Next to printer |
| **bridge.py** | Glue — syncs printer state to Spoolman automatically | `192.168.50.80` |
| **write_tags.py** | Helper — generates NFC tag data from Spoolman inventory | `192.168.50.80` |
| **sync-ams.sh** | Helper — creates Spoolman records from AMS RFID scans | `192.168.50.80` |
| **add-spool.py** | Helper — batch-create spools in Spoolman | `192.168.50.80` |

---

## How It All Works

### Scenario 1: Loading a Bambu Spool into the AMS

You don't need to do anything. It's fully automatic.

1. Insert the spool into an AMS tray
2. The AMS reads the Bambu RFID tag
3. The printer updates its filament settings via MQTT
4. `bridge.py` detects the MQTT change
5. Matches the RFID tag UID to a Spoolman spool (via the `tag` extra field)
6. Updates the spool's `active_tray` and `location` in Spoolman
7. OpenSpoolman dashboard reflects the change

**First time only:** If this is a brand-new Bambu spool the system hasn't seen,
run `sync-ams.sh` on the Debian server to create the Spoolman record and link the
RFID tag. After that, it's automatic forever.

### Scenario 2: Loading a Third-Party Spool on the External Holder

1. Grab the spool (it has an NFC sticker on it)
2. Tap it against the OpenSpool Mini reader
3. The LED turns the filament's color — printer is configured
4. `bridge.py` detects the external spool change via MQTT
5. Matches the filament (by color + material) to a Spoolman spool
6. Updates the spool's `active_tray` to "External" in Spoolman
7. Load the spool and print

**First time only:** The spool needs to exist in Spoolman first. Use
`add-spool.py` to create it:

```bash
./add-spool.py "Inland, PLA, Black, 000000"
```

Then write an NFC tag for it (see "Writing NFC Tags" below).

### Scenario 3: Print Finishes

1. Print completes (or fails)
2. Printer reports filament usage over MQTT
3. `bridge.py` detects the print completion
4. Subtracts the used weight from the spool's `remaining_weight` in Spoolman
5. OpenSpoolman shows the updated remaining weight

### Scenario 4: Checking a Spool from the Shelf (Optional)

If your NFC tags have a second URL record (see "Writing NFC Tags"):

1. Tap the spool with your iPhone
2. Safari opens the spool's OpenSpoolman page
3. See remaining weight, last used date, color, and material info

This is optional — the automated flow doesn't need it.

---

## Initial Setup

### Prerequisites

- Spoolman running (`192.168.50.80:7912`)
- OpenSpoolman running (`192.168.50.80:8001`)
- OpenSpool Mini built and connected to WiFi
- NTAG215 NFC stickers
- Python 3 with `paho-mqtt` and `requests` on the Debian server

### Step 1: Install Dependencies

On the Debian server:

```bash
pip install paho-mqtt requests
```

### Step 2: Configure the Bridge

Edit `bridge.env` with your printer credentials:

```bash
cd ~/Scripts  # or wherever you put these files
nano bridge.env
```

```
PRINTER_IP=192.168.x.x
PRINTER_SERIAL=YOUR_SERIAL_NUMBER
PRINTER_ACCESS_CODE=YOUR_LAN_ACCESS_CODE
SPOOLMAN_URL=http://localhost:7912
OPENSPOOLMAN_URL=https://openspoolman.greenglasst.com
```

**Where to find printer credentials:**
- **IP:** P2S touchscreen → Settings → Network
- **Serial:** P2S touchscreen → Settings → About
- **Access Code:** P2S touchscreen → Settings → Network → LAN Access Code

### Step 3: Test the Bridge

```bash
python3 bridge.py
```

You should see:

```
12:00:00 [INFO] === SpoolBridge ===
12:00:00 [INFO] Printer:  192.168.x.x (serial: XXXXX)
12:00:00 [INFO] Spoolman: http://localhost:7912
12:00:00 [INFO] Spoolman OK: v0.21.0
12:00:00 [INFO] Connected to P2S at 192.168.x.x
12:00:00 [INFO] Subscribed to device/XXXXX/report
```

Load a spool into the AMS or tap one on the OpenSpool reader to verify
it shows up in the logs and updates Spoolman.

### Step 4: Run as a System Service

Once tested, set it up to run on boot:

```bash
sudo tee /etc/systemd/system/openspool-bridge.service << 'EOF'
[Unit]
Description=SpoolBridge
After=network.target docker.service
Wants=docker.service

[Service]
Type=simple
User=trent
WorkingDirectory=/home/trent/Scripts
ExecStart=/usr/bin/python3 /home/trent/Scripts/bridge.py
Restart=always
RestartSec=10
EnvironmentFile=/home/trent/Scripts/bridge.env

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable openspool-bridge
sudo systemctl start openspool-bridge
```

Check status:

```bash
sudo systemctl status openspool-bridge
sudo journalctl -u openspool-bridge -f  # live logs
```

---

## Writing NFC Tags

### Generate Tag Data from Spoolman

On the Debian server (or any machine that can reach Spoolman):

```bash
# Show all non-Bambu spools with their NFC tag JSON
python3 write_tags.py --no-bambu

# Show a specific spool
python3 write_tags.py 16

# Show everything
python3 write_tags.py --all
```

Output looks like:

```
============================================================
  Spool #16:  PLA Black
  Brand: Inland  |  Material: PLA  |  Color: #000000
  Remaining: 1000g
============================================================

  OpenSpool NFC Tag JSON (copy into NFC Tools):
  MIME type: application/json

  {
    "protocol": "openspool",
    "version": "1.0",
    "type": "PLA",
    "color_hex": "000000",
    "brand": "Inland",
    "min_temp": "210",
    "max_temp": "230"
  }

  OpenSpoolman URL (optional 2nd NDEF record):
  https://openspoolman.greenglasst.com/spool_info?spool_id=16
```

### Write the Tag with NFC Tools (iPhone)

**Record 1 — OpenSpool JSON (required):**

1. Open **NFC Tools** app
2. Tap **Write** → **Add a record** → **Custom**
3. Set MIME type to: `application/json`
4. Paste the JSON from `write_tags.py`
5. Tap **Write** → hold NTAG215 sticker to your phone

**Record 2 — OpenSpoolman URL (optional):**

If you also want to tap the spool with your phone to view its page:

1. Tap **Add a record** → **URL**
2. Paste the OpenSpoolman URL from `write_tags.py`
3. Write again to the same tag (NTAG215 has room for both records)

**Stick the tag on the spool.**

### Which Spools Need NFC Tags?

| Spool Type | NFC Tag Needed? | Why |
|------------|----------------|-----|
| **Bambu (in AMS)** | No | AMS reads the built-in RFID automatically |
| **Third-party (external)** | Yes | OpenSpool Mini reads the NFC tag |
| **Third-party (in AMS)** | Optional | AMS can't read it, but you can manually assign in OpenSpoolman. NFC tag lets you use it externally too |

---

## Adding New Spools

### New Bambu Spool

1. Insert into AMS tray
2. Run `sync-ams.sh` on the Debian server — it creates the Spoolman record and links the RFID tag
3. Done — `bridge.py` handles it automatically from now on

### New Third-Party Spool

1. Create it in Spoolman:
   ```bash
   ./add-spool.py "Inland, PLA, Red, ff0000, 18.99"
   ```
2. Generate the NFC tag data:
   ```bash
   ./write_tags.py 17  # use the spool ID from step 1
   ```
3. Write the JSON to an NTAG215 sticker using NFC Tools
4. Stick the tag on the spool

---

## Day-to-Day Workflow

### Starting a Print with External Spool

1. Grab spool from shelf
2. Tap on OpenSpool Mini → LED shows filament color, printer is configured
3. Load spool on external holder
4. Print
5. When done, remaining weight is updated automatically

### Starting a Print with AMS Spool

1. Load spool into AMS tray (or it's already there)
2. Print
3. Everything is tracked automatically

### Checking Inventory

- Open `https://openspoolman.greenglasst.com` in any browser
- See all AMS trays, external spool, remaining weights, last used dates
- Or tap any NFC-tagged spool with your phone to jump to its page

---

## Troubleshooting

### Bridge won't connect to printer

- Verify the printer IP hasn't changed (check touchscreen → Settings → Network)
- Verify the LAN access code is correct
- Make sure the printer is on and connected to WiFi
- The P2S does NOT need Developer Mode enabled

### OpenSpool tap doesn't update Spoolman

- Check `bridge.py` logs for the tap event
- If it says "No Spoolman spool matches", create the spool first with `add-spool.py`
- If multiple spools match (same color + material), the bridge logs a warning — consider renaming or archiving duplicate spools

### AMS spool not recognized

- Run `sync-ams.sh` to link the RFID tag to Spoolman
- Check that OpenSpoolman container is running: `docker ps | grep openspoolman`

### NFC tag not reading on OpenSpool Mini

- Red PN532 reads best at the **corners** of the reader
- Blue PN532 reads best at the **center**
- Make sure PN532 DIP switches are set to SPI mode (switch 1 up, switch 2 down)
- Check wiring and power

### Bridge keeps disconnecting

- Bambu printers allow max 4 concurrent MQTT connections
- Make sure you don't have too many clients connected (Bambu Studio, OpenSpoolman, bridge, OpenSpool Mini = 4)
- If needed, close Bambu Studio when not actively slicing

---

## File Reference

| File | Purpose | Runs On |
|------|---------|---------|
| `bridge.py` | MQTT→Spoolman sync daemon | Debian server (systemd) |
| `bridge.env` | Bridge configuration (printer credentials) | Debian server |
| `write_tags.py` | Generate OpenSpool NFC tag JSON from Spoolman | Debian server or Mac |
| `sync-ams.sh` | Create Spoolman records from AMS RFID scans | Debian server |
| `add-spool.py` | Batch-create spools in Spoolman | Debian server or Mac |
