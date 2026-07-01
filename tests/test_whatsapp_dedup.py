"""Unit tests for WhatsApp dedup cache (_is_duplicate_wa)."""
import pytest

from app.channels.whatsapp import _is_duplicate_wa, _SEEN_WA


def test_first_call_returns_false():
    assert _is_duplicate_wa("wamid.abc123") is False


def test_second_call_same_id_returns_true():
    _is_duplicate_wa("wamid.dup1")
    assert _is_duplicate_wa("wamid.dup1") is True


def test_different_ids_are_not_duplicates():
    assert _is_duplicate_wa("wamid.x1") is False
    assert _is_duplicate_wa("wamid.x2") is False


def test_lru_eviction_after_max_entries():
    from app.channels.whatsapp import _SEEN_WA_MAX
    # Fill cache to max, adding one extra to trigger eviction
    for i in range(_SEEN_WA_MAX + 1):
        _is_duplicate_wa(f"wamid.evict-{i}")
    # First entry (evict-0) should be evicted; still under max
    assert len(_SEEN_WA) == _SEEN_WA_MAX
    assert "wamid.evict-0" not in _SEEN_WA
