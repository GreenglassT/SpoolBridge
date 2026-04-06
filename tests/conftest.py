import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# ── Spoolman URL fixture ──────────────────────────────────────

SPOOLMAN_URL = os.environ.get("SPOOLMAN_URL", "http://localhost:7912")


@pytest.fixture(scope="session")
def spoolman_url():
    """Return Spoolman URL, skip session if unreachable."""
    try:
        r = requests.get(f"{SPOOLMAN_URL}/api/v1/info", timeout=3)
        r.raise_for_status()
    except Exception:
        pytest.skip(f"Spoolman not reachable at {SPOOLMAN_URL}")
    return SPOOLMAN_URL


# ── Cleanup fixture for Spoolman resources ────────────────────

@pytest.fixture
def cleanup_spoolman(spoolman_url):
    """Track and delete Spoolman resources created during a test."""
    created = {"spools": [], "filaments": [], "vendors": []}

    yield created

    for spool_id in reversed(created["spools"]):
        try:
            requests.delete(f"{spoolman_url}/api/v1/spool/{spool_id}")
        except Exception:
            pass
    for fil_id in reversed(created["filaments"]):
        try:
            requests.delete(f"{spoolman_url}/api/v1/filament/{fil_id}")
        except Exception:
            pass
    for vendor_id in reversed(created["vendors"]):
        try:
            requests.delete(f"{spoolman_url}/api/v1/vendor/{vendor_id}")
        except Exception:
            pass


# ── Flask test client ─────────────────────────────────────────

@pytest.fixture
def flask_client(tmp_path):
    """Create a Flask test client with isolated file paths."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import server

    server.SCRIPTS_DIR = tmp_path
    server.app.config["TESTING"] = True
    with server.app.test_client() as client:
        yield client


# ── Temporary scripts dir for bridge file I/O ─────────────────

@pytest.fixture
def tmp_scripts_dir(tmp_path, monkeypatch):
    """Redirect bridge file I/O to a temp directory."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import bridge

    monkeypatch.setattr(bridge, "SCRIPT_DIR", tmp_path)
    monkeypatch.setattr(bridge, "bridge_state_path", tmp_path / "bridge_state.json")
    return tmp_path


# ── Live server for E2E tests ─────────────────────────────────

def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server(spoolman_url):
    """Start server.py on a random port for E2E tests."""
    port = _find_free_port()
    server_path = Path(__file__).parent.parent / "server.py"
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["SPOOLMAN_URL"] = spoolman_url

    proc = subprocess.Popen(
        [sys.executable, str(server_path)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait for server to be ready
    base_url = f"http://localhost:{port}"
    for _ in range(30):
        try:
            requests.get(f"{base_url}/api/config", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        proc.kill()
        pytest.fail(f"Flask server did not start on port {port}")

    yield base_url

    proc.send_signal(signal.SIGTERM)
    proc.wait(timeout=5)


# ── Bridge global state reset ─────────────────────────────────

@pytest.fixture(autouse=True)
def reset_bridge_globals():
    """Reset bridge.py global state between tests."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    try:
        import bridge
        bridge.current_vt_tray = {}
        bridge.current_ams_trays = {}
        bridge.active_spool_id = None
        bridge.last_gcode_state = ""
        bridge.unknown_spools = {}
        bridge.ams_environment = {}
        bridge._activity_line_count = 0
        bridge._ams_initialized = True  # skip init path in tests
        bridge._bridge_state_dirty = True  # allow state writes
        bridge.ams_tray_remain = {}
    except ImportError:
        pass
    yield
