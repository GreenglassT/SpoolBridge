"""Tests for config.py helper functions."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import is_real_tag, extra_quote, extra_unquote


# ── is_real_tag ──────────────────────────────────────────────

def test_is_real_tag_valid():
    assert is_real_tag("6C59DE0200000100") is True


def test_is_real_tag_empty():
    assert is_real_tag("") is False


def test_is_real_tag_none():
    assert is_real_tag(None) is False


def test_is_real_tag_all_zeros():
    assert is_real_tag("0000000000000000") is False


def test_is_real_tag_short_zeros():
    assert is_real_tag("00") is False


def test_is_real_tag_mixed_with_content():
    assert is_real_tag("0000AB0000000000") is True
