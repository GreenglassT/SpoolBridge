# SpoolBridge iOS — NFC Tag Writer

Minimal iOS app for writing OpenSpool NFC tags to NTAG215 stickers. Connects to your SpoolBridge server to fetch spool data, then writes it directly via Core NFC.

## Requirements

- iPhone 7 or later
- iOS 15+
- Xcode 15+
- NTAG215 NFC stickers
- SpoolBridge running on your local network

## Setup

1. Open Xcode → File → New → Project → App
2. Product Name: `SpoolBridge`
3. Interface: SwiftUI, Language: Swift
4. Copy the source files from `Sources/` into your project
5. Add NFC capability:
   - Select your target → Signing & Capabilities → + Capability → Near Field Communication Tag Reading
6. Add to your `Info.plist`:
   ```xml
   <key>NFCReaderUsageDescription</key>
   <string>SpoolBridge uses NFC to write filament data to spool tags.</string>
   ```
7. Update `SpoolBridgeAPI.swift` with your server's local IP address
8. Build and run on your physical iPhone (NFC doesn't work in simulator)

## Architecture

```
Sources/
├── SpoolBridgeApp.swift    # App entry point
├── SpoolBridgeAPI.swift    # HTTP client for SpoolBridge API
├── NFCWriter.swift         # Core NFC tag writing
├── SpoolListView.swift     # Main spool list with pull-to-refresh
├── SpoolRowView.swift      # Individual spool row
├── WriteTagView.swift      # Tag writing screen with status
└── Models.swift            # Spool/Filament data models
```

## How It Works

1. App fetches spools from `http://<your-server>:5555/api/spools`
2. Shows a list of third-party spools (Bambu spools already have RFID)
3. Tap a spool → fetches OpenSpool tag JSON from `/api/tag/<id>`
4. Tap "Write Tag" → hold NTAG215 sticker to iPhone
5. Writes the JSON as an `application/json` NDEF record
6. Spool info URL written as a second NDEF record

## API Endpoints Used

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/spools` | GET | List all spools |
| `/api/tag/<id>` | GET | Get OpenSpool NFC tag JSON for a spool |
| `/api/link-spool` | POST | Link written tag UID back to the spool |
