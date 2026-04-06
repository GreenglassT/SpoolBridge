# SpoolBridge Test Suite Design

## Overview

Comprehensive test suite covering all moving parts of the SpoolBridge application: the MQTT bridge daemon (`bridge.py`), the Flask web server (`server.py`), the CLI tools (`add-spool.py`, `write_tags.py`), and the browser-based dashboard (`static/index.html`).

**Total: ~155 tests** across unit, API, integration, and E2E layers.

## Stack

- **Test runner:** `pytest`
- **Mocking:** `unittest.mock` (stdlib)
- **Browser E2E:** `pytest-playwright` (Python bindings)
- **Dependencies file:** `requirements-test.txt`

```
pytest
pytest-playwright
```

Playwright browsers installed via `playwright install chromium`.

## Architecture Decisions

- **Bridge unit tests** mock all external calls (`requests`, MQTT) to test parsing, matching, and sync logic in isolation. No real Spoolman or printer needed.
- **Server API tests** hit a **real Spoolman instance**. Tests create and clean up their own data.
- **E2E Playwright tests** drive a real browser against a running Flask server + real Spoolman. Flask is launched as a subprocess by a conftest fixture.
- **add-spool.py and write_tags.py** unit tests mock `urllib.request` calls to test pure logic (color resolution, material lookup, CSV parsing, tag generation).

## Project Structure

```
tests/
  conftest.py                     # shared fixtures
  test_bridge_parsers.py          # MQTT message parsing
  test_bridge_matching.py         # spool lookup logic
  test_bridge_sync.py             # state change handlers
  test_bridge_events.py           # activity/print logging, state file
  test_bridge_mqtt.py             # MQTT callback dispatch
  test_bridge_errors.py           # error resilience
  test_bridge_server_integration.py  # bridge<->server contract
  test_server_api.py              # Flask endpoints (real Spoolman)
  test_server_helpers.py          # _read_jsonl, maps, constants
  test_add_spool.py               # CLI spool creator
  test_write_tags.py              # NFC tag generator
  test_load_env.py                # shared .env parser
  test_e2e.py                     # Playwright browser tests
requirements-test.txt
```

## Shared Fixtures (`conftest.py`)

- `spoolman_url`: reads `SPOOLMAN_URL` from env or defaults to `http://localhost:7912`. Checks reachability at session start; skips all dependent tests with clear message if unreachable.
- `flask_app`: creates a Flask test client for `server.py` with test config.
- `tmp_scripts_dir`: temporary directory for activity.json, prints.json, bridge_state.json — isolates file I/O tests.
- `live_server`: starts `server.py` as a subprocess on a random port for E2E tests. Waits for HTTP readiness, tears down after session.
- `page` (Playwright): provided by `pytest-playwright`, one browser context per test.
- `cleanup_spoolman`: fixture that tracks created spool/filament/vendor IDs during a test and deletes them in teardown (reverse order: spools, filaments, vendors).

---

## 1. Bridge Unit Tests (Mocked)

### `test_bridge_parsers.py` — MQTT Message Parsing (~12 tests)

| Test | What it covers |
|------|---------------|
| `test_parse_vt_tray_valid` | Extracts type/color/temps from a full vt_tray payload |
| `test_parse_vt_tray_empty` | Returns None when vt_tray is missing or has no type |
| `test_parse_vt_tray_color_truncation` | Color strings longer than 6 chars get truncated |
| `test_parse_ams_trays_single_unit` | Parses one AMS unit with 4 trays, correct slot keys (A0_0..A0_3) |
| `test_parse_ams_trays_empty_slots` | Skips trays with no tray_type |
| `test_parse_ams_trays_multi_unit` | Multiple AMS units produce distinct slot keys |
| `test_parse_ams_environment_present` | Extracts humidity and temp from first AMS unit |
| `test_parse_ams_environment_missing` | Returns None when AMS list is empty |
| `test_parse_print_finish_success` | FINISH state with weight returns used_grams + status |
| `test_parse_print_finish_failed` | FAILED state returns status "failed" |
| `test_parse_print_finish_no_weight` | FINISH with 0 weight returns used_grams=0 |
| `test_parse_print_finish_running` | Non-finish states return None |

### `test_bridge_matching.py` — Spool Lookup Logic (~8 tests)

All tests mock `requests.get` to return controlled spool lists.

| Test | What it covers |
|------|---------------|
| `test_find_by_color_material_exact` | Exact color + material match returns spool |
| `test_find_by_color_material_prefix` | "PLA Basic" matches "PLA" (prefix matching) |
| `test_find_by_color_material_strip_ff` | Color hex "FF0000FF" strips trailing "FF" before compare |
| `test_find_by_color_material_no_match` | Returns None when nothing matches |
| `test_find_by_color_material_multiple` | Multiple matches returns first with warning log |
| `test_find_by_tag_found` | Finds spool by RFID tag UID in extra.tag |
| `test_find_by_tag_not_found` | Returns None for unknown tag |
| `test_find_by_tag_empty` | Returns None when tag_uid is empty string |

### `test_bridge_sync.py` — State Change Handlers (~10 tests)

Mock `requests.get/patch` and `find_spool_by_*` helpers. Reset global state between tests.

| Test | What it covers |
|------|---------------|
| `test_vt_tray_change_matched` | External spool change finds matching spool, updates tray, logs event |
| `test_vt_tray_change_unmatched` | No match sets active_spool_id to None, logs warning |
| `test_vt_tray_no_change` | Same vt_tray data twice: no action on second call |
| `test_ams_change_by_tag` | AMS tray with RFID tag finds spool, updates tray |
| `test_ams_change_unknown_tag` | Unknown tag adds to unknown_spools dict |
| `test_ams_change_no_tag_color_match` | Third-party spool matched by color+material |
| `test_ams_change_no_change` | Same tray data twice: no action |
| `test_print_finish_with_active_spool` | Deducts weight, logs event + print record |
| `test_print_finish_no_active_spool` | Logs event but no weight update |
| `test_print_finish_zero_weight` | No weight update when used_grams is 0 |

### `test_bridge_events.py` — File I/O (~5 tests)

Use `tmp_scripts_dir` fixture to isolate file writes.

| Test | What it covers |
|------|---------------|
| `test_log_event_creates_file` | activity.json created on first event |
| `test_log_event_appends` | Multiple events append as JSON-lines |
| `test_log_event_caps_at_200` | After 250+ entries, trims to 200 |
| `test_log_print_creates_file` | prints.json created on first print |
| `test_write_bridge_state` | Writes correct JSON structure to bridge_state.json |

### `test_bridge_mqtt.py` — MQTT Callback Dispatch (~6 tests)

Mock the MQTT client and handler functions.

| Test | What it covers |
|------|---------------|
| `test_on_connect_subscribes` | on_connect subscribes to correct topic and publishes pushall |
| `test_on_message_dispatches_vt` | Message with vt_tray triggers handle_vt_tray_change |
| `test_on_message_dispatches_ams` | Message with AMS data triggers handle_ams_change |
| `test_on_message_state_transition` | RUNNING->FINISH fires handle_print_finish once |
| `test_on_message_no_double_fire` | FINISH->FINISH doesn't re-fire |
| `test_on_message_bad_json` | Non-JSON payload is silently ignored |

### `test_bridge_errors.py` — Error Resilience (~6 tests)

| Test | What it covers |
|------|---------------|
| `test_update_spool_tray_api_failure` | requests.patch raises -> logged, no crash |
| `test_update_spool_weight_api_failure` | requests.patch raises -> logged, no crash |
| `test_find_spool_api_failure` | requests.get raises -> propagates (caller handles) |
| `test_log_event_write_failure` | File write fails -> logged, no crash |
| `test_write_bridge_state_failure` | File write fails -> logged, no crash |
| `test_on_disconnect_writes_state` | on_disconnect writes connected=false to bridge_state.json |

---

## 2. Bridge<->Server Integration Tests (~3 tests)

### `test_bridge_server_integration.py`

Tests the contract between bridge file outputs and server file readers. No external services needed.

| Test | What it covers |
|------|---------------|
| `test_bridge_state_format` | `write_bridge_state()` output matches what `printer_status()` expects |
| `test_activity_json_format` | `log_event()` output parseable by `_read_jsonl()` |
| `test_prints_json_format` | `log_print()` output parseable by `_read_jsonl()` |

---

## 3. Server API Tests (Real Spoolman) (~35 tests)

### `test_server_api.py`

Uses Flask test client. All requests proxy to real Spoolman. `cleanup_spoolman` fixture handles teardown.

| Test | Endpoint | What it covers |
|------|----------|---------------|
| **Spools CRUD** | | |
| `test_get_spools` | `GET /api/spools` | Returns list from Spoolman |
| `test_create_spool_bambu` | `POST /api/spools` | Creates vendor + filament + spool for Bambu brand |
| `test_create_spool_inland` | `POST /api/spools` | Uses Inland brand defaults (145g spool weight) |
| `test_create_spool_reuses_vendor` | `POST /api/spools` | Second create reuses existing vendor |
| `test_create_spool_reuses_filament` | `POST /api/spools` | Same material+color+vendor reuses filament |
| `test_update_spool` | `PATCH /api/spools/<id>` | Updates remaining_weight |
| `test_update_spool_rejects_disallowed` | `PATCH /api/spools/<id>` | Disallowed fields (filament_id) silently dropped |
| `test_delete_spool` | `DELETE /api/spools/<id>` | Deletes and confirms gone |
| **Filaments CRUD** | | |
| `test_get_filaments` | `GET /api/filaments` | Returns list |
| `test_create_filament` | `POST /api/filaments` | Creates and returns filament |
| `test_update_filament` | `PATCH /api/filaments/<id>` | Updates name/material |
| `test_delete_filament` | `DELETE /api/filaments/<id>` | Deletes and confirms |
| **Vendors CRUD** | | |
| `test_get_vendors` | `GET /api/vendors` | Returns list |
| `test_create_vendor` | `POST /api/vendors` | Creates vendor |
| `test_update_vendor` | `PATCH /api/vendors/<id>` | Updates name |
| `test_delete_vendor` | `DELETE /api/vendors/<id>` | Deletes vendor |
| **Tray Status** | | |
| `test_tray_status_empty` | `GET /api/tray-status` | All trays null when no spools assigned |
| `test_tray_status_with_assigned` | `GET /api/tray-status` | Shows spool data for assigned trays |
| `test_tray_status_normalizes_old_keys` | `GET /api/tray-status` | Legacy "serial_0_1" keys normalize to "A2" |
| **Assign Tray** | | |
| `test_assign_tray_valid` | `POST /api/spools/<id>/assign-tray` | Sets tray + location |
| `test_assign_tray_invalid` | `POST /api/spools/<id>/assign-tray` | Rejects invalid tray name (400) |
| `test_assign_tray_unassign` | `POST /api/spools/<id>/assign-tray` | Empty tray clears assignment |
| **Link Spool** | | |
| `test_link_spool` | `POST /api/link-spool` | Creates spool with NFC tag + tray |
| `test_link_spool_no_tag` | `POST /api/link-spool` | Returns 400 without tag_uid |
| `test_link_spool_reuses_filament` | `POST /api/link-spool` | Existing filament match reused |
| **NFC Tag** | | |
| `test_get_tag` | `GET /api/tag/<id>` | Returns OpenSpool JSON with correct material mapping + temps |
| **Activity/Prints** | | |
| `test_get_activity` | `GET /api/activity` | Returns reversed JSON-lines from activity.json |
| `test_get_activity_empty` | `GET /api/activity` | Returns [] when file missing |
| `test_get_prints` | `GET /api/prints` | Returns print records |
| **Printer/Bridge Status** | | |
| `test_printer_status_no_file` | `GET /api/printer-status` | Returns disconnected when no state file |
| `test_printer_status_with_file` | `GET /api/printer-status` | Returns bridge state from JSON file |
| **Bridge Actions** | | |
| `test_bridge_action_invalid` | `POST /api/bridge/invalid` | Returns 400 for actions not in allowlist |
| **Config** | | |
| `test_get_config` | `GET /api/config` | Returns spoolman_url, brands, openspoolman_url |

### `test_server_helpers.py` (~5 tests)

| Test | What it covers |
|------|---------------|
| `test_read_jsonl_empty` | Returns [] for missing file |
| `test_read_jsonl_with_data` | Parses lines, reverses order |
| `test_read_jsonl_limit` | Respects limit parameter |
| `test_material_map_coverage` | All common materials map to expected OpenSpool types |
| `test_default_temps_coverage` | All mapped types have temp ranges defined |

---

## 4. CLI Tool Unit Tests (Mocked)

### `test_add_spool.py` (~20 tests)

Mock `urllib.request.urlopen` for all API calls.

| Test | What it covers |
|------|---------------|
| **`resolve_color`** | |
| `test_resolve_color_by_name` | "jade white" -> "ffffff" |
| `test_resolve_color_by_hex` | "ff7eb3" passes through unchanged |
| `test_resolve_color_strips_hash` | "#ff0000" -> "ff0000" |
| `test_resolve_color_strips_alpha` | "ff0000ff" -> "ff0000" |
| `test_resolve_color_fuzzy` | "dark" partial matches a dark color |
| `test_resolve_color_unknown` | "xyzzy" -> "" |
| **`get_material_info`** | |
| `test_material_exact_match` | "PLA Basic" returns correct density/temps |
| `test_material_case_insensitive` | "pla basic" still matches |
| `test_material_prefix_match` | "PLA+ Tough" matches "PLA+" entry |
| `test_material_unknown_defaults_pla` | Unknown material falls back to PLA |
| **`get_spoolman_material`** | |
| `test_spoolman_material_with_parent` | "PLA Basic" -> "PLA" (uses parent) |
| `test_spoolman_material_no_parent` | "PETG" -> "PETG" (no parent) |
| **`parse_entry`** | |
| `test_parse_entry_full` | All 6 CSV fields parsed correctly |
| `test_parse_entry_minimal` | 3 fields (brand, material, color): rest defaults |
| `test_parse_entry_resolves_color` | Color name auto-resolved when hex is empty |
| `test_parse_entry_too_few_fields` | <3 fields returns {} |
| `test_parse_entry_quoted_csv` | CSV with commas in quoted fields handled |
| `test_parse_entry_price_float` | Price string converted to float |
| **`find_filament`** (mocked API) | |
| `test_find_filament_by_material_color` | Matches vendor + material + color |
| `test_find_filament_by_name` | Falls back to name match when hex differs |
| `test_find_filament_no_match` | Returns None |
| **`get_or_create_vendor`** (mocked API) | |
| `test_get_vendor_existing` | Finds existing vendor, returns ID |
| `test_create_vendor_new` | Creates vendor when not found |
| `test_get_vendor_unknown_brand` | Unknown brand uses raw string as vendor name |

### `test_write_tags.py` (~5 tests)

Mock `urllib.request.urlopen` for Spoolman API calls.

| Test | What it covers |
|------|---------------|
| `test_spool_to_openspool_json_basic` | PLA spool -> correct OpenSpool JSON |
| `test_spool_to_openspool_json_material_mapping` | "petg hf" maps to "PETG" type |
| `test_spool_to_openspool_json_custom_temp` | Spool with settings_extruder_temp uses +/-10 range |
| `test_spool_to_openspool_json_default_temp` | No custom temp -> uses DEFAULT_TEMPS |
| `test_spool_to_openspool_json_unknown_material` | Unknown material uppercased as fallback |

### `test_load_env.py` (~6 tests)

Uses temp files to test the `load_env()` function shared across bridge.py, server.py, write_tags.py.

| Test | What it covers |
|------|---------------|
| `test_load_env_basic` | KEY=value parsed correctly |
| `test_load_env_quoted` | KEY="value" strips quotes |
| `test_load_env_comments` | Lines starting with # skipped |
| `test_load_env_blank_lines` | Empty lines skipped |
| `test_load_env_no_override` | Existing env vars not overwritten (setdefault behavior) |
| `test_load_env_missing_file` | No crash when bridge.env doesn't exist |

---

## 5. E2E Playwright Browser Tests (~45 tests)

### `test_e2e.py`

Each test gets a fresh browser page. Tests that create data clean up via API calls in teardown. Flask server started by `live_server` conftest fixture.

| Test | What it covers |
|------|---------------|
| **Navigation & Layout** | |
| `test_dashboard_loads` | Page loads, title "SpoolBridge", header + tabs visible |
| `test_tab_navigation` | Click each tab -> correct panel visible, others hidden |
| `test_theme_toggle` | Toggle switches between dark/light, data-theme attribute changes |
| **Dashboard Tab** | |
| `test_dashboard_kpis` | KPI cards show total spools, low stock count, material breakdown |
| `test_dashboard_tray_cards` | AMS tray strip shows A1-A4 + External slots |
| `test_dashboard_activity_feed` | Activity list populates (seed activity.json before test) |
| `test_dashboard_low_stock_section` | Low-stock spools highlighted when remaining < threshold |
| **Dashboard: Bridge/Printer Status** | |
| `test_printer_status_connected` | Seed bridge_state.json with connected=true -> shows connected indicator |
| `test_printer_status_disconnected` | No state file -> shows disconnected |
| `test_ams_environment_display` | Humidity + temp from bridge state shown |
| `test_unknown_spools_display` | Unknown spools listed with "Link to Spoolman" button |
| `test_link_unknown_spool` | Click "Link to Spoolman" -> spool created, toast success, unknown removed |
| **Spools Tab** | |
| `test_spool_list_loads` | Cards render with color swatch, material, weight |
| `test_create_spool_via_form` | Fill brand/material/color form, submit -> new spool in list |
| `test_spool_search_filter` | Type in search -> list filters by name/material |
| `test_spool_archive_toggle` | Show archived checkbox toggles visibility of archived spools |
| `test_expand_spool_detail` | Click card -> detail section expands with edit controls |
| `test_edit_spool_weight` | Enter new weight, save -> weight updated |
| `test_edit_spool_location` | Change location, save -> location updated |
| `test_assign_tray_dropdown` | Select tray from dropdown, click Fill -> tray assigned, toast shows |
| `test_archive_spool` | Archive button -> spool hidden (unless "show archived" checked) |
| `test_delete_spool_confirm` | Delete button -> confirm dialog -> spool removed, toast shown |
| `test_export_csv` | Click "Export CSV" -> download triggered |
| **Filaments Tab** | |
| `test_filament_list_loads` | Filaments listed with material, color, vendor |
| `test_create_filament` | Fill form, submit -> new filament appears |
| `test_search_filaments` | Search input filters the filament list |
| `test_delete_filament` | Delete -> removed from list |
| **Vendors Tab** | |
| `test_vendor_list_loads` | Vendor names listed |
| `test_create_vendor` | Enter name, click Add -> vendor appears |
| `test_delete_vendor` | Delete -> vendor removed |
| **NFC Tags Tab** | |
| `test_tag_spool_list` | Spools listed with selectable cards |
| `test_tag_search_filter` | Search filters tag spool list |
| `test_select_spool_shows_tag` | Click spool -> OpenSpool JSON + URL displayed |
| `test_copy_tag_json` | "Copy JSON" button -> clipboard populated (check via toast) |
| `test_copy_tag_url` | "Copy URL" button -> clipboard populated |
| **Toast Notifications** | |
| `test_success_toast_appears` | After successful CRUD -> green-bordered toast with message |
| `test_error_toast_appears` | After failed operation -> red-bordered toast |
| `test_toast_auto_dismisses` | Toast disappears after timeout |
| **Batch Add** | |
| `test_batch_add_spools` | Add multiple spools via batch form -> all created |
| **Bridge Controls** | |
| `test_bridge_start_stop_restart` | Bridge control buttons send correct API calls (mock systemctl) |
| `test_sync_ams_button` | Sync AMS button fires and shows output |
| **Error States** | |
| `test_empty_state_no_spools` | Dashboard + spool tab show empty states when no data |

---

## Prerequisites

To run the full test suite:

1. **Python packages:** `pip install -r requirements-test.txt`
2. **Playwright browsers:** `playwright install chromium`
3. **Spoolman running:** at `http://localhost:7912` (or set `SPOOLMAN_URL`)
4. **No printer needed:** bridge unit tests are fully mocked

To run subsets:
```bash
pytest tests/test_bridge_*.py          # bridge unit tests only (no Spoolman needed)
pytest tests/test_add_spool.py         # add-spool tests only (no Spoolman needed)
pytest tests/test_server_*.py          # server API tests (needs Spoolman)
pytest tests/test_e2e.py               # E2E browser tests (needs Spoolman)
pytest tests/ -m "not e2e"             # everything except E2E
```

## Test Markers

- `@pytest.mark.e2e` — all Playwright browser tests
- `@pytest.mark.spoolman` — tests requiring real Spoolman
- No marker — fully self-contained unit tests
