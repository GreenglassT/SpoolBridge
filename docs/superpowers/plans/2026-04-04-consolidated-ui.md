# Consolidated UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make SpoolBridge the only UI needed — add AMS tray view, activity feed, print history, spool edit/delete to the web UI at `:5555`.

**Architecture:** bridge.py gets event logging (activity.json + prints.json). server.py gets new API endpoints for tray status, activity, prints, spool edit/delete. index.html dashboard is redesigned with AMS grid, activity feed, low stock, and new tabs for print history and spool detail/edit.

**Tech Stack:** Python 3 / Flask, vanilla JS, Spoolman REST API

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `bridge.py` | Modify | Add event logging (activity.json, prints.json) |
| `server.py` | Modify | Add 5 new API endpoints |
| `static/index.html` | Modify | Redesign dashboard, add print history tab, add spool edit/delete |

---

### Task 1: Add Event Logging to bridge.py

**Files:**
- Modify: `bridge.py`

Event logging writes to two JSON-lines files when tray changes, external taps, or prints complete. These files are the data source for the web UI's activity feed and print history.

- [ ] **Step 1: Add log_event and log_print functions to bridge.py**

Add after the `update_spool_weight` function (after line 168), before the MQTT MESSAGE PARSING section:

```python
# ============================================================
# EVENT LOGGING — feeds the web UI activity feed + print history
# ============================================================

DATA_DIR = Path(__file__).parent

def log_event(event_type, tray="", spool_id=None, spool_name="", detail=""):
    """Append an event to activity.json (capped at 200 entries)."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "type": event_type,
        "tray": tray,
        "spool_id": spool_id,
        "spool_name": spool_name,
        "detail": detail,
    }
    path = DATA_DIR / "activity.json"
    try:
        lines = path.read_text().splitlines() if path.exists() else []
        lines.append(json.dumps(entry))
        # Keep last 200 entries
        if len(lines) > 200:
            lines = lines[-200:]
        path.write_text("\n".join(lines) + "\n")
    except Exception as e:
        log.error(f"Failed to write activity log: {e}")


def log_print(spool_id, spool_name, material, color_hex, grams_used, status):
    """Append a print completion to prints.json."""
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "spool_id": spool_id,
        "spool_name": spool_name,
        "material": material,
        "color_hex": color_hex,
        "grams_used": grams_used,
        "status": status,
    }
    path = DATA_DIR / "prints.json"
    try:
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        log.error(f"Failed to write print log: {e}")
```

- [ ] **Step 2: Add log_event calls to handle_vt_tray_change**

In `handle_vt_tray_change` (line 240), after the `update_spool_tray` call on line 265, add:

```python
        log_event("external_tap", tray="External", spool_id=spool["id"],
                  spool_name=f.get("name", "?"),
                  detail=f"{f.get('material', '?')} #{new_vt['color']}")
```

And after the `log.warning` on line 269, add:

```python
        log_event("external_tap", tray="External",
                  detail=f"Unmatched: {new_vt['type']} #{new_vt['color']}")
```

- [ ] **Step 3: Add log_event calls to handle_ams_change**

In `handle_ams_change` (line 274), after the `update_spool_tray` call on line 294, add:

```python
                log_event("tray_load", tray=f"A{tray_num}", spool_id=spool["id"],
                          spool_name=f.get("name", "?"),
                          detail=f"tag {tag_uid[:8]}...")
```

After the `update_spool_tray` call on line 309 (color match branch), add:

```python
                log_event("tray_load", tray=f"A{tray_num}", spool_id=spool["id"],
                          spool_name=f.get("name", "?"),
                          detail=f"color match")
```

- [ ] **Step 4: Add log_event and log_print calls to handle_print_finish**

In `handle_print_finish` (line 314), replace the body after `if used <= 0: return` with:

```python
    if active_spool_id:
        log.info(f"Print finished, used {used:.1f}g from spool #{active_spool_id}")
        # Get spool info for logging before updating weight
        try:
            spool = sm_get(f"spool/{active_spool_id}")
            f = spool.get("filament", {})
            spool_name = f.get("name", "?")
            material = f.get("material", "?")
            color_hex = f.get("color_hex", "000000")
        except Exception:
            spool_name = f"Spool #{active_spool_id}"
            material = "?"
            color_hex = "000000"

        update_spool_weight(active_spool_id, used)
        log_event("print_finish", spool_id=active_spool_id,
                  spool_name=spool_name, detail=f"{used:.1f}g used")
        log_print(active_spool_id, spool_name, material, color_hex, used, "finished")
    else:
        log.info(f"Print finished, used {used:.1f}g but no active spool tracked")
        log_event("print_finish", detail=f"{used:.1f}g used, no spool tracked")
```

- [ ] **Step 5: Handle FAILED prints too**

In `parse_print_finish` (line 218), the function already detects both FINISH and FAILED states but returns the same data. Update `handle_print_finish` to accept the gcode_state. Change `parse_print_finish` to also return the state:

Replace lines 224-232 of `parse_print_finish`:

```python
    gcode_state = mc_print.get("gcode_state", "")
    if gcode_state not in ("FINISH", "FAILED"):
        return None

    total_used = mc_print.get("total_weight", 0)
    if total_used and total_used > 0:
        return {"used_grams": total_used, "status": gcode_state.lower()}

    return None
```

And in `handle_print_finish`, use `finish_data.get("status", "finished")` for the status in both `log_event` and `log_print`:

```python
        status = finish_data.get("status", "finished")
        log_event(f"print_{status}", spool_id=active_spool_id,
                  spool_name=spool_name, detail=f"{used:.1f}g used")
        log_print(active_spool_id, spool_name, material, color_hex, used, status)
```

- [ ] **Step 6: Verify bridge.py still starts correctly**

Run: `cd /home/trent/spoolbridge && timeout 10 ./venv/bin/python3 bridge.py 2>&1 || true`

Expected: Connects to printer, subscribes, shows AMS tray info. No import errors.

- [ ] **Step 7: Restart the bridge service**

Run: `sudo systemctl restart openspool-bridge && sleep 2 && sudo systemctl status openspool-bridge`

Expected: Active (running). Verify no errors in logs.

- [ ] **Step 8: Commit**

```bash
git add bridge.py
git commit -m "feat: add event logging to bridge.py for activity feed and print history"
```

---

### Task 2: Add New API Endpoints to server.py

**Files:**
- Modify: `server.py`

Five new endpoints: tray-status, activity, prints, spool PATCH, spool DELETE.

- [ ] **Step 1: Add tray-status endpoint**

Add after the `/api/vendors` endpoint (after line 161):

```python
@app.route("/api/tray-status")
def get_tray_status():
    """Return current tray assignments from Spoolman extra fields."""
    spools = http_requests.get(f"{SPOOLMAN_URL}/api/v1/spool").json()

    trays = {}
    for s in spools:
        extra = s.get("extra", {})
        active_tray = extra.get("active_tray", "").strip('"')
        if active_tray:
            f = s.get("filament", {})
            trays[active_tray] = {
                "spool_id": s["id"],
                "name": f.get("name", "Unknown"),
                "material": f.get("material", "?"),
                "color_hex": f.get("color_hex", "333333"),
                "brand": (f.get("vendor") or {}).get("name", "?"),
                "remaining_weight": s.get("remaining_weight", 0),
                "total_weight": f.get("weight", 1000),
            }

    # Ensure all 4 AMS slots exist (A1-A4)
    for i in range(1, 5):
        key = f"A{i}"
        if key not in trays:
            trays[key] = None

    # Ensure External slot exists
    if "External" not in trays:
        trays["External"] = None

    return jsonify(trays)
```

- [ ] **Step 2: Add activity endpoint**

Add after the tray-status endpoint:

```python
@app.route("/api/activity")
def get_activity():
    """Return recent activity events from bridge log."""
    path = SCRIPTS_DIR / "activity.json"
    if not path.exists():
        return jsonify([])

    try:
        lines = path.read_text().strip().splitlines()
        events = [json.loads(line) for line in lines if line.strip()]
        events.reverse()  # newest first
        return jsonify(events[:50])
    except Exception:
        return jsonify([])
```

- [ ] **Step 3: Add prints endpoint**

Add after the activity endpoint:

```python
@app.route("/api/prints")
def get_prints():
    """Return print history from bridge log."""
    path = SCRIPTS_DIR / "prints.json"
    if not path.exists():
        return jsonify([])

    try:
        lines = path.read_text().strip().splitlines()
        prints = [json.loads(line) for line in lines if line.strip()]
        prints.reverse()  # newest first
        return jsonify(prints)
    except Exception:
        return jsonify([])
```

- [ ] **Step 4: Add spool PATCH endpoint**

Add after the prints endpoint:

```python
@app.route("/api/spools/<int:spool_id>", methods=["PATCH"])
def update_spool(spool_id):
    """Update a spool in Spoolman (remaining_weight, location)."""
    data = request.json
    allowed = {}
    if "remaining_weight" in data:
        allowed["remaining_weight"] = float(data["remaining_weight"])
    if "location" in data:
        allowed["location"] = str(data["location"])

    if not allowed:
        return jsonify({"error": "No valid fields to update"}), 400

    r = http_requests.patch(
        f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}",
        json=allowed,
    )
    if r.status_code >= 400:
        return jsonify({"error": r.text}), r.status_code
    return jsonify(r.json())
```

- [ ] **Step 5: Add spool DELETE endpoint**

Add after the PATCH endpoint:

```python
@app.route("/api/spools/<int:spool_id>", methods=["DELETE"])
def delete_spool(spool_id):
    """Delete a spool from Spoolman."""
    r = http_requests.delete(f"{SPOOLMAN_URL}/api/v1/spool/{spool_id}")
    if r.status_code >= 400:
        return jsonify({"error": r.text}), r.status_code
    return jsonify({"deleted": spool_id})
```

- [ ] **Step 6: Add json import to server.py**

`json` is needed for parsing activity/prints logs. Add to the imports at line 16:

```python
import json
```

(Already imported — verify it's there. If not, add it.)

- [ ] **Step 7: Verify server starts**

Run: `cd /home/trent/spoolbridge && timeout 5 ./venv/bin/python3 server.py 2>&1 || true`

Expected: Server starts on port 5555 with no errors.

- [ ] **Step 8: Test endpoints**

Run: `curl -s http://localhost:5555/api/tray-status | python3 -m json.tool | head -20`

Expected: JSON with keys A1, A2, A3, A4, External (some null, some with spool data).

Run: `curl -s http://localhost:5555/api/activity | python3 -m json.tool`

Expected: Empty list `[]` (no events yet).

Run: `curl -s http://localhost:5555/api/prints | python3 -m json.tool`

Expected: Empty list `[]` (no prints yet).

- [ ] **Step 9: Restart web service**

Run: `sudo systemctl restart spoolbridge-web && sleep 2 && sudo systemctl status spoolbridge-web`

Expected: Active (running).

- [ ] **Step 10: Commit**

```bash
git add server.py
git commit -m "feat: add tray-status, activity, prints, edit, delete API endpoints"
```

---

### Task 3: Redesign Dashboard in index.html

**Files:**
- Modify: `static/index.html`

Replace the KPI row and dashboard spools section with the AMS tray grid, activity feed, and low stock alerts.

- [ ] **Step 1: Replace dashboard panel HTML**

Replace the dashboard panel (lines 516-523):

```html
<div class="panel active" id="panel-dashboard">
    <div class="kpi-row" id="kpis"></div>

    <div class="section-header">
      <span class="section-title">Spool Inventory</span>
    </div>
    <div id="dashboard-spools" class="spool-grid"></div>
  </div>
```

With:

```html
  <div class="panel active" id="panel-dashboard">
    <!-- AMS Tray View -->
    <div style="display:flex; gap:16px; margin-bottom:24px; align-items:flex-start; flex-wrap:wrap;">
      <div>
        <div class="section-title" style="margin-bottom:8px;">AMS 2 Pro</div>
        <div id="ams-grid" style="display:grid; grid-template-columns:1fr 1fr; gap:6px; background:var(--bg-panel); border:1px solid var(--border); border-radius:var(--radius); padding:10px; width:280px;">
        </div>
      </div>
      <div>
        <div class="section-title" style="margin-bottom:8px; color:var(--accent);">External</div>
        <div id="external-slot" style="width:130px;">
        </div>
      </div>
    </div>

    <!-- Activity + Low Stock -->
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:16px;">
      <div>
        <div class="section-header">
          <span class="section-title">Recent Activity</span>
        </div>
        <div class="log-output" id="activity-feed" style="max-height:280px;">No events yet.</div>
      </div>
      <div>
        <div class="section-header">
          <span class="section-title" id="low-stock-title">Low Stock</span>
        </div>
        <div id="low-stock-list"></div>
      </div>
    </div>
  </div>
```

- [ ] **Step 2: Add Print History tab button**

In the nav tabs section (line 507-513), add a Print History tab before Tools:

```html
    <button class="tab" data-panel="history" role="tab">Print History</button>
```

- [ ] **Step 3: Add Print History panel HTML**

Add before the Tools panel (before line 618):

```html
  <!-- ═══ Print History ═══ -->
  <div class="panel" id="panel-history">
    <div class="section-header">
      <span class="section-title">Print History</span>
    </div>
    <div id="print-history-list"></div>
    <div class="empty" id="print-history-empty">No prints recorded yet. History appears after prints complete.</div>
  </div>
```

- [ ] **Step 4: Add CSS for tray slots and print history**

Add before the closing `</style>` tag (before line 491):

```css
/* ── AMS Tray Slots ────────────────────────────────────────── */
.tray-slot {
  background: var(--bg-card);
  border-radius: var(--radius);
  padding: 12px;
  text-align: center;
  min-height: 90px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  transition: border-color 0.15s;
}
.tray-slot.occupied { border: 1px solid var(--border); }
.tray-slot.empty-tray { border: 1px dashed var(--border); }
.tray-slot .tray-swatch {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  border: 2px solid var(--border);
  margin-bottom: 6px;
}
.tray-slot .tray-name {
  font-size: 0.8rem;
  font-weight: 500;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 110px;
}
.tray-slot .tray-meta {
  font-size: 0.72rem;
  color: var(--text-muted);
}
.tray-slot .tray-weight {
  font-family: var(--mono);
  font-size: 0.72rem;
  color: var(--accent);
  margin-top: 2px;
}

.external-slot {
  background: var(--bg-panel);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  padding: 12px;
  text-align: center;
  min-height: 90px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

/* ── Print History Entries ──────────────────────────────────── */
.print-entry {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--bg-panel);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  margin-bottom: 6px;
}
.print-entry .print-swatch {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  border: 2px solid var(--border);
  flex-shrink: 0;
}
.print-entry .print-info { flex: 1; min-width: 0; }
.print-entry .print-name {
  font-weight: 500;
  font-size: 0.85rem;
}
.print-entry .print-meta {
  font-size: 0.75rem;
  color: var(--text-muted);
}
.print-entry .print-grams {
  font-family: var(--mono);
  font-size: 0.85rem;
  font-weight: 500;
  color: var(--accent);
  white-space: nowrap;
}
.print-entry .print-status-failed {
  color: var(--red);
}

/* ── Spool Detail Panel ────────────────────────────────────── */
.spool-detail {
  background: var(--bg-panel);
  border: 1px solid var(--accent);
  border-radius: var(--radius);
  padding: 20px;
  margin-top: 12px;
  margin-bottom: 12px;
}
.spool-detail .detail-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-bottom: 16px;
}
.spool-detail .detail-item .form-label {
  margin-bottom: 2px;
}
.spool-detail .detail-value {
  font-size: 0.85rem;
  color: var(--text);
}
.spool-detail .detail-actions {
  display: flex;
  gap: 8px;
  margin-top: 12px;
}

/* ── Low Stock Cards ───────────────────────────────────────── */
.low-stock-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 14px;
  background: var(--bg-panel);
  border-radius: var(--radius);
  margin-bottom: 6px;
}
.low-stock-card.critical { border: 1px solid var(--red); border-left: 3px solid var(--red); }
.low-stock-card.warning { border: 1px solid var(--yellow); border-left: 3px solid var(--yellow); }
.low-stock-card .low-stock-swatch {
  width: 20px;
  height: 20px;
  border-radius: 50%;
  border: 1px solid var(--border);
  flex-shrink: 0;
}
.low-stock-card .low-stock-name { font-size: 0.82rem; color: var(--text); }
.low-stock-card .low-stock-weight {
  font-family: var(--mono);
  font-size: 0.78rem;
  margin-left: auto;
  white-space: nowrap;
}
.low-stock-card.critical .low-stock-weight { color: var(--red); }
.low-stock-card.warning .low-stock-weight { color: var(--yellow); }

@media (max-width: 768px) {
  #panel-dashboard > div:last-child { grid-template-columns: 1fr; }
}
```

- [ ] **Step 5: Replace the renderDashboard function in JavaScript**

Replace the entire `renderDashboard` function (lines 721-757) with:

```javascript
function renderDashboard() {
  fetchTrayStatus();
  fetchActivity();
  renderLowStock();
}

async function fetchTrayStatus() {
  try {
    const trays = await api('/api/tray-status');
    renderAmsGrid(trays);
    renderExternalSlot(trays);
  } catch(e) {
    console.error('Failed to fetch tray status', e);
  }
}

function renderAmsGrid(trays) {
  const grid = document.getElementById('ams-grid');
  let html = '';
  for (let i = 1; i <= 4; i++) {
    const key = 'A' + i;
    const t = trays[key];
    if (t) {
      const pct = Math.round((t.remaining_weight / t.total_weight) * 100);
      const barColor = pct > 30 ? 'var(--green)' : pct > 10 ? 'var(--yellow)' : 'var(--red)';
      html += `
        <div class="tray-slot occupied">
          <div class="tray-swatch" style="background:#${t.color_hex};"></div>
          <div class="tray-name">${escHtml(t.name)}</div>
          <div class="tray-meta">${escHtml(t.brand)}</div>
          <div class="tray-weight">${t.remaining_weight.toFixed(0)}g</div>
          <div class="weight-bar" style="width:100%; margin-top:4px;">
            <div class="weight-bar-fill" style="width:${pct}%; background:${barColor};"></div>
          </div>
        </div>`;
    } else {
      html += `
        <div class="tray-slot empty-tray">
          <div class="tray-swatch" style="background:var(--bg-input); border-style:dashed;"></div>
          <div class="tray-meta" style="color:var(--text-dim);">Empty</div>
          <div class="tray-meta" style="color:var(--text-dim);">Tray ${i}</div>
        </div>`;
    }
  }
  grid.innerHTML = html;
}

function renderExternalSlot(trays) {
  const el = document.getElementById('external-slot');
  const t = trays['External'];
  if (t) {
    const pct = Math.round((t.remaining_weight / t.total_weight) * 100);
    const barColor = pct > 30 ? 'var(--green)' : pct > 10 ? 'var(--yellow)' : 'var(--red)';
    el.innerHTML = `
      <div class="external-slot">
        <div class="tray-swatch" style="background:#${t.color_hex}; border:2px solid var(--border);"></div>
        <div class="tray-name">${escHtml(t.name)}</div>
        <div class="tray-meta">${escHtml(t.brand)}</div>
        <div class="tray-weight">${t.remaining_weight.toFixed(0)}g</div>
        <div class="weight-bar" style="width:100%; margin-top:4px;">
          <div class="weight-bar-fill" style="width:${pct}%; background:${barColor};"></div>
        </div>
      </div>`;
  } else {
    el.innerHTML = `
      <div class="external-slot" style="border-style:dashed;">
        <div class="tray-swatch" style="background:var(--bg-input); border-style:dashed;"></div>
        <div class="tray-meta" style="color:var(--text-dim);">No spool</div>
      </div>`;
  }
}

async function fetchActivity() {
  try {
    const events = await api('/api/activity');
    const el = document.getElementById('activity-feed');
    if (events.length === 0) {
      el.textContent = 'No events yet.';
      return;
    }
    const dotColors = {
      print_finish: 'var(--green)', print_failed: 'var(--red)',
      tray_load: 'var(--blue)', external_tap: 'var(--accent)',
    };
    el.innerHTML = events.slice(0, 20).map(e => {
      const dot = dotColors[e.type] || 'var(--text-dim)';
      const time = (e.ts || '').split('T')[1] || '';
      const name = e.spool_name || '';
      const detail = e.detail || '';
      return `<div><span style="color:var(--text-dim);">${escHtml(time)}</span> <span style="color:${dot};">●</span> ${escHtml(name)} ${escHtml(detail)}</div>`;
    }).join('');
  } catch(e) {
    console.error('Failed to fetch activity', e);
  }
}

function renderLowStock() {
  const low = spools.filter(s => {
    const w = s.remaining_weight || 0;
    return w > 0 && w < 200;
  });
  const el = document.getElementById('low-stock-list');
  const title = document.getElementById('low-stock-title');

  if (low.length === 0) {
    title.textContent = 'Low Stock';
    el.innerHTML = '<div class="empty" style="padding:20px;">All stocked up.</div>';
    return;
  }

  title.textContent = `Low Stock (${low.length})`;
  el.innerHTML = low.map(s => {
    const f = s.filament || {};
    const w = s.remaining_weight || 0;
    const cls = w < 100 ? 'critical' : 'warning';
    return `
      <div class="low-stock-card ${cls}">
        <div class="low-stock-swatch" style="background:#${f.color_hex || '333'};"></div>
        <div class="low-stock-name">${escHtml(f.name || 'Unknown')}</div>
        <div class="low-stock-weight">${w.toFixed(0)}g</div>
      </div>`;
  }).join('');
}
```

- [ ] **Step 6: Add auto-refresh polling**

Add to the `DOMContentLoaded` handler, after the initial `checkBridge()` call (around line 669):

```javascript
  // Auto-refresh dashboard every 5 seconds
  setInterval(async () => {
    if (document.querySelector('#panel-dashboard.active')) {
      await loadSpools();
      renderDashboard();
    }
  }, 5000);
```

- [ ] **Step 7: Commit**

```bash
git add static/index.html
git commit -m "feat: redesign dashboard with AMS tray view, activity feed, low stock"
```

---

### Task 4: Add Spool Detail, Edit, and Delete to Spools Tab

**Files:**
- Modify: `static/index.html`

Clicking a spool card in the Spools tab expands an inline detail panel with edit and delete buttons.

- [ ] **Step 1: Add spool detail state and click handler**

Add to the State section (around line 653):

```javascript
let selectedSpoolId = null;
```

- [ ] **Step 2: Update spoolCard function to support detail expansion**

Replace the `spoolCard` function (lines 763-796) with:

```javascript
function spoolCard(s, selectable = false, expandable = false) {
  const f = s.filament || {};
  const color = f.color_hex || '333';
  const name = f.name || 'Unknown';
  const material = f.material || '?';
  const brand = f.vendor?.name || '?';
  const remaining = s.remaining_weight || 0;
  const total = f.weight || 1000;
  const pct = Math.round((remaining / total) * 100);
  const barColor = pct > 30 ? 'var(--green)' : pct > 10 ? 'var(--yellow)' : 'var(--red)';
  const selected = selectable && selectedTagSpool === s.id ? ' selected' : '';
  const onclick = selectable ? ` onclick="selectTagSpool(${s.id})"` :
                  expandable ? ` onclick="toggleSpoolDetail(${s.id})"` : '';

  let html = `
    <div class="spool-card${selected}"${onclick}>
      <div class="spool-swatch" style="background:#${color};"></div>
      <div class="spool-info">
        <div class="spool-name">${escHtml(name)}</div>
        <div class="spool-meta">
          <span>${escHtml(brand)}</span>
          <span>${escHtml(material)}</span>
          <span class="spool-id">#${s.id}</span>
        </div>
        <div style="display:flex; align-items:center; justify-content:space-between; margin-top:4px;">
          <span class="spool-weight">${remaining.toFixed(0)}g</span>
          <span class="dim" style="font-size:0.72rem;">${pct}%</span>
        </div>
        <div class="weight-bar">
          <div class="weight-bar-fill" style="width:${pct}%; background:${barColor};"></div>
        </div>
      </div>
    </div>`;

  if (expandable && selectedSpoolId === s.id) {
    const extra = s.extra || {};
    const activeTray = (extra.active_tray || '').replace(/"/g, '') || 'None';
    const tag = (extra.tag || '').replace(/"/g, '') || 'None';
    const location = s.location || 'Unknown';
    const price = f.price ? `$${f.price.toFixed(2)}` : '—';

    html += `
    <div class="spool-detail">
      <div class="detail-grid">
        <div class="detail-item">
          <div class="form-label">Location</div>
          <div class="detail-value">${escHtml(location)}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">Active Tray</div>
          <div class="detail-value">${escHtml(activeTray)}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">Material</div>
          <div class="detail-value">${escHtml(material)}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">Color</div>
          <div class="detail-value">#${escHtml(color)}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">Brand</div>
          <div class="detail-value">${escHtml(brand)}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">Price</div>
          <div class="detail-value">${price}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">RFID Tag</div>
          <div class="detail-value mono" style="font-size:0.72rem;">${escHtml(tag)}</div>
        </div>
        <div class="detail-item">
          <div class="form-label">Remaining / Total</div>
          <div class="detail-value mono">${remaining.toFixed(0)}g / ${total.toFixed(0)}g</div>
        </div>
      </div>

      <div style="display:flex; gap:8px; align-items:center; margin-bottom:12px;">
        <label class="form-label" style="margin:0;">Update Weight:</label>
        <input type="number" id="edit-weight-${s.id}" value="${remaining.toFixed(0)}" style="width:80px;" step="1" min="0">
        <span class="muted">g</span>
        <button class="btn btn-primary btn-sm" onclick="editSpoolWeight(${s.id})">Save</button>
      </div>

      <div class="detail-actions">
        <button class="btn btn-ghost btn-sm red" onclick="deleteSpool(${s.id}, '${escHtml(name)}')">Delete Spool</button>
      </div>
    </div>`;
  }

  return html;
}
```

- [ ] **Step 3: Add toggleSpoolDetail, editSpoolWeight, and deleteSpool functions**

Add to the JavaScript, after the `renderSpools` function:

```javascript
function toggleSpoolDetail(id) {
  selectedSpoolId = selectedSpoolId === id ? null : id;
  renderSpools();
}

async function editSpoolWeight(id) {
  const input = document.getElementById(`edit-weight-${id}`);
  const weight = parseFloat(input.value);
  if (isNaN(weight) || weight < 0) {
    toast('Invalid weight', 'error');
    return;
  }
  try {
    await api(`/api/spools/${id}`, { method: 'PATCH', body: { remaining_weight: weight } });
    toast(`Spool #${id} updated to ${weight}g`, 'success');
    await loadSpools();
    renderSpools();
    renderDashboard();
  } catch(e) {
    toast('Failed to update spool', 'error');
  }
}

async function deleteSpool(id, name) {
  if (!confirm(`Delete "${name}" (spool #${id})? This cannot be undone.`)) return;
  try {
    await api(`/api/spools/${id}`, { method: 'DELETE' });
    toast(`Spool #${id} deleted`, 'success');
    selectedSpoolId = null;
    await loadSpools();
    renderSpools();
    renderDashboard();
    renderTagSpools();
  } catch(e) {
    toast('Failed to delete spool', 'error');
  }
}
```

- [ ] **Step 4: Update renderSpools to pass expandable=true**

In the `renderSpools` function, change the line that renders spool cards (line 826):

```javascript
  document.getElementById('spools-list').innerHTML = filtered.length
    ? filtered.map(s => spoolCard(s, false, true)).join('')
    : '<div class="empty">No spools match your filters.</div>';
```

- [ ] **Step 5: Commit**

```bash
git add static/index.html
git commit -m "feat: add spool detail view with inline edit and delete"
```

---

### Task 5: Add Print History Tab

**Files:**
- Modify: `static/index.html`

- [ ] **Step 1: Add renderPrintHistory function**

Add to the JavaScript:

```javascript
async function renderPrintHistory() {
  try {
    const prints = await api('/api/prints');
    const list = document.getElementById('print-history-list');
    const empty = document.getElementById('print-history-empty');

    if (prints.length === 0) {
      list.innerHTML = '';
      empty.style.display = 'block';
      return;
    }

    empty.style.display = 'none';
    list.innerHTML = prints.map(p => {
      const date = (p.ts || '').replace('T', ' ');
      const statusClass = p.status === 'failed' ? ' print-status-failed' : '';
      const statusLabel = p.status === 'failed' ? ' (FAILED)' : '';
      return `
        <div class="print-entry">
          <div class="print-swatch" style="background:#${p.color_hex || '333'};"></div>
          <div class="print-info">
            <div class="print-name${statusClass}">${escHtml(p.spool_name || 'Unknown')}${statusLabel}</div>
            <div class="print-meta">${escHtml(p.material || '?')} · ${escHtml(date)}</div>
          </div>
          <div class="print-grams${statusClass}">-${p.grams_used.toFixed(1)}g</div>
        </div>`;
    }).join('');
  } catch(e) {
    console.error('Failed to load print history', e);
  }
}
```

- [ ] **Step 2: Call renderPrintHistory on tab switch**

Update the `setupTabs` function to load print history when its tab is activated. Replace the tab click handler:

```javascript
function setupTabs() {
  document.querySelectorAll('.tab').forEach(tab => {
    tab.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
      document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
      tab.classList.add('active');
      document.getElementById('panel-' + tab.dataset.panel).classList.add('active');

      // Lazy-load print history
      if (tab.dataset.panel === 'history') {
        renderPrintHistory();
      }
    });
  });
}
```

- [ ] **Step 3: Verify everything works**

Open http://192.168.50.80:5555 in browser. Check:
- Dashboard shows AMS tray grid with current spool data
- Activity feed shows "No events yet" (will populate after next tray change/print)
- Low stock section shows spools under 200g or "All stocked up"
- Spools tab: clicking a spool shows detail panel with weight edit and delete
- Print History tab shows "No prints recorded yet"
- All existing tabs (Write Tags, Add Spool, Tools) still work

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: add print history tab and lazy-load on tab switch"
```

---

### Task 6: Final Integration and Restart Services

- [ ] **Step 1: Restart both services**

```bash
sudo systemctl restart openspool-bridge
sudo systemctl restart spoolbridge-web
```

- [ ] **Step 2: Verify both services running**

```bash
sudo systemctl status openspool-bridge spoolbridge-web
```

Expected: Both active (running).

- [ ] **Step 3: Test live data flow**

If possible, load or unload a spool from the AMS. Then check:
- Dashboard AMS grid updates within 5 seconds
- Activity feed shows the tray change event
- Bridge logs show the event: `sudo journalctl -u openspool-bridge -n 5 --no-pager`

- [ ] **Step 4: Final commit with all files**

```bash
git add -A
git commit -m "feat: consolidated SpoolBridge UI — AMS tray view, activity feed, print history, spool edit/delete"
```
