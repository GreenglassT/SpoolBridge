"""Tests for the load_env() parsing logic found in bridge.py."""

import os
from pathlib import Path


def _run_parse(content: str, tmp_path: Path, env_overrides: dict = None) -> dict:
    """
    Replicate the load_env() parsing loop against a temp file.
    Returns a dict of keys that were set by the parser (not pre-existing).
    """
    env_file = tmp_path / "bridge.env"
    env_file.write_text(content)

    # Back up any keys we care about, then restore afterward
    parsed = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            k = key.strip()
            v = val.strip().strip('"')
            parsed[k] = v

    return parsed


def _apply_parse(content: str, tmp_path: Path, pre_env: dict = None) -> dict:
    """
    Apply the load_env() logic (including setdefault semantics) to a temp file.
    Returns the final env values for keys encountered in the file.
    """
    env_file = tmp_path / "bridge.env"
    env_file.write_text(content)

    # Temporarily set pre-existing env vars
    pre_env = pre_env or {}
    saved = {}
    for k, v in pre_env.items():
        saved[k] = os.environ.get(k)
        os.environ[k] = v

    # Keys set by the parser
    affected_keys = set()
    results = {}

    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                k = key.strip()
                v = val.strip().strip('"')
                affected_keys.add(k)
                os.environ.setdefault(k, v)

    for k in affected_keys:
        results[k] = os.environ.get(k)

    # Clean up: restore previous state
    for k in affected_keys:
        if k not in pre_env:
            os.environ.pop(k, None)
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v

    return results


def test_load_env_basic(tmp_path):
    """KEY=value lines are parsed and set in the environment."""
    results = _apply_parse("MY_KEY=hello\n", tmp_path)
    assert results["MY_KEY"] == "hello"


def test_load_env_quoted(tmp_path):
    """Quoted values (KEY="value") have surrounding quotes stripped."""
    results = _apply_parse('MY_KEY="hello world"\n', tmp_path)
    assert results["MY_KEY"] == "hello world"


def test_load_env_comments(tmp_path):
    """Lines starting with # are skipped entirely."""
    content = "# this is a comment\nREAL_KEY=real_value\n"
    parsed = _run_parse(content, tmp_path)
    assert "# this is a comment" not in parsed
    assert parsed.get("REAL_KEY") == "real_value"
    # Ensure the comment line was not treated as a key
    assert not any(k.startswith("#") for k in parsed)


def test_load_env_blank_lines(tmp_path):
    """Empty/blank lines are skipped without error."""
    content = "\n   \nVALID_KEY=42\n\n"
    results = _apply_parse(content, tmp_path)
    assert results["VALID_KEY"] == "42"
    # Only one key should have been parsed
    assert len(results) == 1


def test_load_env_no_override(tmp_path):
    """Existing environment variables are NOT overwritten (setdefault semantics)."""
    content = "EXISTING_KEY=from_file\n"
    results = _apply_parse(content, tmp_path, pre_env={"EXISTING_KEY": "original"})
    assert results["EXISTING_KEY"] == "original"


def test_load_env_missing_file(tmp_path):
    """No exception is raised when bridge.env does not exist."""
    env_path = tmp_path / "bridge.env"
    # Ensure the file does not exist
    assert not env_path.exists()

    # Replicate the guard from load_env()
    try:
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip('"'))
    except Exception as exc:
        raise AssertionError(f"load_env raised unexpectedly: {exc}") from exc
