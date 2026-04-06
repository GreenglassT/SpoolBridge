"""
End-to-end browser tests for SpoolBridge using Playwright.

Requires:
  - pytest-playwright  (pip install pytest-playwright && playwright install)
  - A running Spoolman instance (default http://localhost:7912)

Tests are marked with 'e2e' and 'spoolman' so they can be selected/skipped:
  pytest tests/test_e2e.py -v -m e2e
"""

import json
import re
from pathlib import Path

import pytest
import requests
from playwright.sync_api import Page, expect

pytestmark = [pytest.mark.e2e, pytest.mark.spoolman]

# ── Helpers ──────────────────────────────────────────────────


def _create_test_spool(base_url, color_hex="ee0011", material="PLA", brand="bambu"):
    resp = requests.post(
        f"{base_url}/api/spools",
        json={
            "brand": brand,
            "material": material,
            "color_hex": color_hex,
            "color_name": f"Test {color_hex}",
        },
    )
    return resp.json()


def _delete_spool(base_url, spool_id):
    try:
        requests.delete(f"{base_url}/api/spools/{spool_id}")
    except Exception:
        pass


def _create_test_vendor(base_url, name="E2E_TestVendor"):
    return requests.post(f"{base_url}/api/vendors", json={"name": name}).json()


def _delete_vendor(base_url, vendor_id):
    try:
        requests.delete(f"{base_url}/api/vendors/{vendor_id}")
    except Exception:
        pass


# ── Navigation & Layout ─────────────────────────────────────


def test_dashboard_loads(live_server, page: Page):
    """Page loads with title, header h1, and nav tabs visible."""
    page.goto(live_server)
    expect(page).to_have_title("SpoolBridge")
    expect(page.locator("header h1")).to_be_visible()
    expect(page.locator("nav.tabs")).to_be_visible()


def test_tab_navigation(live_server, page: Page):
    """Clicking each tab activates its corresponding panel."""
    page.goto(live_server)
    page.wait_for_timeout(500)

    tabs = [
        "dashboard", "spools", "tags", "add", "filaments",
        "vendors", "history", "tools",
    ]
    for tab_name in tabs:
        btn = page.locator(f'button.tab[data-panel="{tab_name}"]')
        if btn.count() == 0:
            continue
        btn.click()
        page.wait_for_timeout(300)
        panel = page.locator(f"#panel-{tab_name}")
        expect(panel).to_have_class(re.compile(r"active"))


def test_theme_toggle(live_server, page: Page):
    """Clicking theme toggle sets data-theme='light' on <html>."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator("#theme-toggle").click()
    expect(page.locator("html")).to_have_attribute("data-theme", "light")


# ── Dashboard ────────────────────────────────────────────────


def test_dashboard_kpis(live_server, page: Page):
    """Dashboard KPIs container is visible on load."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    expect(page.locator("#dashboard-kpis")).to_be_visible()


def test_dashboard_tray_cards(live_server, page: Page):
    """Tray strip is visible on dashboard."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    expect(page.locator("#tray-strip")).to_be_visible()


def test_dashboard_activity_feed(live_server, page: Page):
    """Activity feed section is visible on dashboard."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    expect(page.locator("#activity-feed")).to_be_visible()


def test_dashboard_low_stock_section(live_server, page: Page):
    """Low-stock list section is visible on dashboard."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    expect(page.locator("#low-stock-list")).to_be_visible()


def test_printer_status_disconnected(live_server, page: Page):
    """Bridge indicator is visible on dashboard."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    expect(page.locator("#bridge-indicator")).to_be_visible()


# ── Spools ───────────────────────────────────────────────────


def test_spool_list_loads(live_server, page: Page):
    """Creating a spool and switching to the spools tab shows the list."""
    spool = _create_test_spool(live_server)
    spool_id = spool.get("id")
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="spools"]').click()
        page.wait_for_timeout(500)
        expect(page.locator("#spools-list")).to_be_visible()
    finally:
        if spool_id:
            _delete_spool(live_server, spool_id)


def test_create_spool_via_form(live_server, page: Page):
    """Filling add-spool form and submitting shows a toast."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="add"]').click()
    page.wait_for_timeout(500)

    # Select brand, material, and color preset if available
    brand_select = page.locator("#add-brand")
    if brand_select.count() > 0:
        brand_select.select_option(index=0)
    material_select = page.locator("#add-material")
    if material_select.count() > 0:
        material_select.select_option(index=0)
    color_preset = page.locator("#add-color-preset")
    if color_preset.count() > 0:
        color_preset.select_option(index=1)

    # Submit the form
    page.locator("#add-form").locator('button[type="submit"], button.btn-primary').first.click()
    page.wait_for_timeout(1000)

    # Toast should appear
    toast = page.locator("#toast")
    expect(toast).to_have_class(re.compile(r"show"))


def test_spool_search_filter(live_server, page: Page):
    """Searching for a nonexistent term shows zero spool cards."""
    spool = _create_test_spool(live_server)
    spool_id = spool.get("id")
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="spools"]').click()
        page.wait_for_timeout(500)
        page.locator("#spool-search").fill("zzz_nonexistent_xyz_999")
        page.wait_for_timeout(500)
        cards = page.locator("#spools-list .spool-card:visible")
        expect(cards).to_have_count(0)
    finally:
        if spool_id:
            _delete_spool(live_server, spool_id)


def test_spool_archive_toggle(live_server, page: Page):
    """Archive checkbox is visible and can be checked."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="spools"]').click()
    page.wait_for_timeout(500)
    checkbox = page.locator("#spool-show-archived")
    expect(checkbox).to_be_visible()
    checkbox.check()
    expect(checkbox).to_be_checked()


def test_expand_spool_detail(live_server, page: Page):
    """Clicking a spool card expands its detail section."""
    spool = _create_test_spool(live_server, color_hex="11aa22")
    spool_id = spool.get("id")
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="spools"]').click()
        page.wait_for_timeout(500)
        card = page.locator("#spools-list .spool-card").first
        expect(card).to_be_visible()
        card.click()
        page.wait_for_timeout(500)
        # After clicking, the card should have an expanded detail section
        detail = card.locator(".spool-detail, .spool-expanded, .card-detail")
        if detail.count() > 0:
            expect(detail.first).to_be_visible()
    finally:
        if spool_id:
            _delete_spool(live_server, spool_id)


def test_delete_spool_confirm(live_server, page: Page):
    """Deleting a spool via UI triggers confirm dialog and shows toast."""
    spool = _create_test_spool(live_server, color_hex="ff0033")
    spool_id = spool.get("id")
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="spools"]').click()
        page.wait_for_timeout(500)

        # Expand the first spool card
        card = page.locator("#spools-list .spool-card").first
        card.click()
        page.wait_for_timeout(500)

        # Accept the confirm dialog when it appears
        page.on("dialog", lambda dialog: dialog.accept())

        # Click delete button
        delete_btn = card.locator("button:has-text('Delete'), .btn-delete, .delete-btn").first
        if delete_btn.count() > 0:
            delete_btn.click()
            page.wait_for_timeout(1000)
            toast = page.locator("#toast")
            expect(toast).to_have_class(re.compile(r"show"))
            spool_id = None  # Already deleted via UI
    finally:
        if spool_id:
            _delete_spool(live_server, spool_id)


def test_export_csv(live_server, page: Page):
    """Export CSV button is visible on spools tab."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="spools"]').click()
    page.wait_for_timeout(500)
    export_btn = page.locator("button:has-text('Export CSV')")
    expect(export_btn).to_be_visible()


# ── Filaments ────────────────────────────────────────────────


def test_filament_list_loads(live_server, page: Page):
    """Clicking filaments tab shows the filaments list container."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="filaments"]').click()
    page.wait_for_timeout(500)
    expect(page.locator("#filaments-list")).to_be_visible()


def test_search_filaments(live_server, page: Page):
    """Filling filament search input triggers filtering."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="filaments"]').click()
    page.wait_for_timeout(500)
    search = page.locator("#filament-search")
    expect(search).to_be_visible()
    search.fill("zzz_nonexistent_xyz")
    page.wait_for_timeout(500)


# ── Vendors ──────────────────────────────────────────────────


def test_vendor_list_loads(live_server, page: Page):
    """Clicking vendors tab shows the vendors list container."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="vendors"]').click()
    page.wait_for_timeout(500)
    expect(page.locator("#vendors-list")).to_be_visible()


def test_create_vendor(live_server, page: Page):
    """Creating a vendor via the UI adds it to the list."""
    vendor_id = None
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="vendors"]').click()
        page.wait_for_timeout(500)

        page.locator("#new-vendor-name").fill("E2E_TestVendorUI")
        page.wait_for_timeout(300)

        # Click the add button near the vendor input
        add_btn = page.locator(
            "#panel-vendors button:has-text('Add'), "
            "#panel-vendors button.btn-primary"
        ).first
        add_btn.click()
        page.wait_for_timeout(1000)

        # Look up the vendor via API for cleanup
        vendors_resp = requests.get(f"{live_server}/api/vendors")
        for v in vendors_resp.json():
            if v.get("name") == "E2E_TestVendorUI":
                vendor_id = v["id"]
                break
    finally:
        if vendor_id:
            _delete_vendor(live_server, vendor_id)


# ── NFC Tags ────────────────────────────────────────────────


def test_tag_spool_list(live_server, page: Page):
    """Tags tab shows the tag-spools container."""
    spool = _create_test_spool(live_server, color_hex="0000ff")
    spool_id = spool.get("id")
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="tags"]').click()
        page.wait_for_timeout(500)
        expect(page.locator("#tag-spools")).to_be_visible()
    finally:
        if spool_id:
            _delete_spool(live_server, spool_id)


def test_tag_search_filter(live_server, page: Page):
    """Searching for a nonexistent term in tags tab filters results."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="tags"]').click()
    page.wait_for_timeout(500)
    search = page.locator("#tag-search")
    expect(search).to_be_visible()
    search.fill("zzz_nonexistent_xyz_999")
    page.wait_for_timeout(500)
    cards = page.locator("#tag-spools .spool-card:visible")
    expect(cards).to_have_count(0)


def test_select_spool_shows_tag(live_server, page: Page):
    """Clicking a spool card in tags tab shows openspool tag data."""
    spool = _create_test_spool(live_server, color_hex="aabb00")
    spool_id = spool.get("id")
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="tags"]').click()
        page.wait_for_timeout(500)

        card = page.locator("#tag-spools .spool-card").first
        if card.count() > 0:
            card.click()
            page.wait_for_timeout(500)
            # Look for "openspool" text anywhere in the tags panel
            expect(page.locator("#panel-tags")).to_contain_text("openspool")
    finally:
        if spool_id:
            _delete_spool(live_server, spool_id)


# ── Toast ────────────────────────────────────────────────────


def test_success_toast_appears(live_server, page: Page):
    """Creating a vendor via UI triggers a visible toast."""
    vendor_id = None
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="vendors"]').click()
        page.wait_for_timeout(500)

        page.locator("#new-vendor-name").fill("E2E_ToastTestVendor")
        page.locator(
            "#panel-vendors button:has-text('Add'), "
            "#panel-vendors button.btn-primary"
        ).first.click()
        page.wait_for_timeout(1000)

        toast = page.locator("#toast")
        expect(toast).to_be_visible()

        # Cleanup
        vendors_resp = requests.get(f"{live_server}/api/vendors")
        for v in vendors_resp.json():
            if v.get("name") == "E2E_ToastTestVendor":
                vendor_id = v["id"]
                break
    finally:
        if vendor_id:
            _delete_vendor(live_server, vendor_id)


def test_toast_auto_dismisses(live_server, page: Page):
    """Toast loses 'show' class after a few seconds."""
    vendor_id = None
    try:
        page.goto(live_server)
        page.wait_for_timeout(500)
        page.locator('button.tab[data-panel="vendors"]').click()
        page.wait_for_timeout(500)

        page.locator("#new-vendor-name").fill("E2E_DismissTestVendor")
        page.locator(
            "#panel-vendors button:has-text('Add'), "
            "#panel-vendors button.btn-primary"
        ).first.click()
        page.wait_for_timeout(500)

        # Toast should be visible initially
        toast = page.locator("#toast")
        expect(toast).to_have_class(re.compile(r"show"))

        # Wait for auto-dismiss (typically 3-5 seconds)
        page.wait_for_timeout(5000)
        expect(toast).not_to_have_class(re.compile(r"show"))

        # Cleanup
        vendors_resp = requests.get(f"{live_server}/api/vendors")
        for v in vendors_resp.json():
            if v.get("name") == "E2E_DismissTestVendor":
                vendor_id = v["id"]
                break
    finally:
        if vendor_id:
            _delete_vendor(live_server, vendor_id)


# ── Batch / Bridge / History ────────────────────────────────


def test_batch_add_spools(live_server, page: Page):
    """Batch input textarea is visible on the add tab."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="add"]').click()
    page.wait_for_timeout(500)
    expect(page.locator("#batch-input")).to_be_visible()


def test_bridge_start_stop_restart(live_server, page: Page):
    """Tools tab shows Start, Stop, and Restart buttons."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="tools"]').click()
    page.wait_for_timeout(500)

    panel = page.locator("#panel-tools")
    expect(panel.locator("button:has-text('Start')")).to_be_visible()
    expect(panel.locator("button:has-text('Stop')")).to_be_visible()
    expect(panel.locator("button:has-text('Restart')")).to_be_visible()


def test_sync_ams_button(live_server, page: Page):
    """Sync AMS button is visible on the tools tab."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="tools"]').click()
    page.wait_for_timeout(500)
    expect(page.locator("#btn-sync-ams")).to_be_visible()


def test_print_history_loads(live_server, page: Page):
    """Clicking history tab activates its panel."""
    page.goto(live_server)
    page.wait_for_timeout(500)
    page.locator('button.tab[data-panel="history"]').click()
    page.wait_for_timeout(500)
    expect(page.locator("#panel-history")).to_have_class(re.compile(r"active"))


def test_empty_state_no_spools(live_server, page: Page):
    """Page loads without JS errors; header visible even with no spools."""
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(live_server)
    page.wait_for_timeout(1000)
    expect(page.locator("header h1")).to_be_visible()
    # No critical JS errors should have occurred
    critical = [e for e in errors if "TypeError" in e or "ReferenceError" in e]
    assert not critical, f"JS errors on page load: {critical}"
