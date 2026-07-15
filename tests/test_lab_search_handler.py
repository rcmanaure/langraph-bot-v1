"""Tests for the staff-facing lab search handler (phase 1). DB is mocked —
no live Postgres required. Covers the mandatory regression tests called out
in the plan: user_id (not chat_id) session keying, staff_secret rebinding
rejection, and filter parsing without any LLM call."""
import hashlib
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.lab_search_handler import (
    _PENDING,
    _SESSIONS,
    _StaffSession,
    _try_unlock,
    handle_staff_message,
    is_staff_session_active,
    parse_filters,
)

TENANT_SLUG = "sp-labs"
TENANT_ID = 7
RAW_SECRET = "a" * 48  # long enough to pass the 16-char short-circuit


@pytest.fixture(autouse=True)
def clear_state():
    _SESSIONS.clear()
    _PENDING.clear()
    yield
    _SESSIONS.clear()
    _PENDING.clear()


def _ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _secret_row(secret_id=1, label="Dra. Pérez", bound_user_id=None, revoked=False):
    row = MagicMock()
    row.id = secret_id
    row.label = label
    row.secret_hash = hashlib.sha256(RAW_SECRET.encode()).hexdigest()
    row.bound_user_id = bound_user_id
    row.revoked_at = "2026-01-01" if revoked else None
    return row


def _session_returning(row):
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    return session


# ── parse_filters — no LLM, deterministic ────────────────────────────────────

def test_parse_filters_plain_text_is_patient_name():
    assert parse_filters("Elba Zacarias") == {"patient_name": "Elba Zacarias"}


def test_parse_filters_structured_fields():
    filters = parse_filters("nombre: Elba Zacarias; tipo: hemograma")
    assert filters == {"patient_name": "Elba Zacarias", "test_type": "hemograma"}


def test_parse_filters_unknown_key_falls_back_to_patient_name():
    """An unrecognized `key:` alias yields no structured fields, so the
    whole raw text is used as a patient-name search rather than silently
    discarding the query."""
    assert parse_filters("foo: bar") == {"patient_name": "foo: bar"}


# ── Unlock: bind, rebind rejection, no-match ─────────────────────────────────

@pytest.mark.asyncio
async def test_unlock_binds_unbound_secret_to_first_user():
    row = _secret_row(bound_user_id=None)
    session = _session_returning(row)
    with patch("app.channels.lab_search_handler.AsyncSessionLocal", return_value=_ctx(session)):
        outcome = await _try_unlock(TENANT_ID, TENANT_SLUG, "user-1", RAW_SECRET)
    assert outcome.result == "bound"
    assert outcome.label == "Dra. Pérez"
    session.execute.assert_awaited()  # UPDATE bound_user_id


@pytest.mark.asyncio
async def test_unlock_rejects_secret_already_bound_to_different_user():
    """Regression: a real, valid secret typed by a SECOND user_id must be
    rejected, not silently re-bound — otherwise one leaked secret gives real
    accountability to nobody."""
    row = _secret_row(bound_user_id="user-1")
    session = _session_returning(row)
    with patch("app.channels.lab_search_handler.AsyncSessionLocal", return_value=_ctx(session)):
        outcome = await _try_unlock(TENANT_ID, TENANT_SLUG, "user-2", RAW_SECRET)
    assert outcome.result == "rejected"


@pytest.mark.asyncio
async def test_unlock_same_user_rebinding_own_secret_succeeds():
    row = _secret_row(bound_user_id="user-1")
    session = _session_returning(row)
    with patch("app.channels.lab_search_handler.AsyncSessionLocal", return_value=_ctx(session)):
        outcome = await _try_unlock(TENANT_ID, TENANT_SLUG, "user-1", RAW_SECRET)
    assert outcome.result == "bound"


@pytest.mark.asyncio
async def test_unlock_no_match_for_short_text_never_hits_db():
    """Short-circuit: ordinary patient text never even queries the DB."""
    with patch("app.channels.lab_search_handler.AsyncSessionLocal") as mock_session_local:
        outcome = await _try_unlock(TENANT_ID, TENANT_SLUG, "user-1", "hola")
    assert outcome.result == "no_match"
    mock_session_local.assert_not_called()


@pytest.mark.asyncio
async def test_unlock_no_match_for_revoked_secret():
    session = _session_returning(None)  # revoked secrets excluded by the WHERE clause
    with patch("app.channels.lab_search_handler.AsyncSessionLocal", return_value=_ctx(session)):
        outcome = await _try_unlock(TENANT_ID, TENANT_SLUG, "user-1", RAW_SECRET)
    assert outcome.result == "no_match"


# ── Regression: session keyed on user_id, NOT chat_id (group chats) ─────────

# ── End-to-end search: realistic file shape survives the whole handler ─────
# The advisor flagged that no test exercised search_files → _handle_search
# with a real candidate shape — this closes that gap.

@pytest.mark.asyncio
async def test_search_with_realistic_file_renders_preview_and_confirm_delivers():
    session_key = (TENANT_SLUG, "user-1")
    # _get_session does a real TTL check on the object, so use the real class:
    _SESSIONS[session_key] = _StaffSession(secret_id=1, label="Dra. Pérez",
                                             expires_at=time.monotonic() + 1800)

    real_candidate = {
        "id": "1SViEjFX6AluyalXKo3DAxqkSqDXSNp_d",
        "name": "JUN0049-SP-ELBA ZACARIAS.pdf",
        "modifiedTime": "2026-07-14T14:04:32.273Z",
        "webViewLink": "https://drive.google.com/file/d/1SViEjFX6AluyalXKo3DAxqkSqDXSNp_d/view",
        "size": "229136",
    }
    send = AsyncMock()

    with (
        patch("app.channels.lab_search_handler._get_google_credentials",
              new=AsyncMock(return_value=object())),
        patch("app.channels.lab_search_handler.drive_search_files",
              new=AsyncMock(return_value=([real_candidate], False))),
        patch("app.channels.lab_search_handler._write_audit", new=AsyncMock()) as mock_audit,
    ):
        handled = await handle_staff_message(
            TENANT_ID, TENANT_SLUG, "user-1", "chat-1", "Elba Zacarias", send
        )
        assert handled is True

        preview_msg = str(send.call_args_list[-1])
        assert "JUN0049-SP-ELBA ZACARIAS.pdf" in preview_msg
        assert "Confirmás el envío" in preview_msg
        assert session_key in _PENDING

        # Confirm delivery
        handled_confirm = await handle_staff_message(
            TENANT_ID, TENANT_SLUG, "user-1", "chat-1", "sí", send
        )

    assert handled_confirm is True
    delivery_msg = str(send.call_args_list[-1])
    assert "drive.google.com" in delivery_msg
    assert session_key not in _PENDING
    assert mock_audit.await_count == 2  # one at search, one at delivery


@pytest.mark.asyncio
async def test_multi_filter_zero_results_falls_back_to_name_only():
    """doctor/test_type go through fullText (best-effort — see drive.py). If
    a scanned PDF isn't body-indexed, a multi-filter search comes back empty
    even though the name-only search would have found it. Must retry
    name-only before giving up."""
    session_key = (TENANT_SLUG, "user-1")
    _SESSIONS[session_key] = _StaffSession(secret_id=1, label="Dra. Pérez",
                                             expires_at=time.monotonic() + 1800)

    real_candidate = {"id": "1", "name": "JUN0049-SP-ELBA ZACARIAS.pdf",
                       "modifiedTime": "2026-07-14T14:04:32.273Z", "webViewLink": "https://x", "size": "1"}
    send = AsyncMock()

    call_count = 0

    async def fake_search(creds, filters, limit):
        nonlocal call_count
        call_count += 1
        if "test_type" in filters:
            return [], False  # first call: multi-filter finds nothing
        return [real_candidate], False  # retry: name-only finds it

    with (
        patch("app.channels.lab_search_handler._get_google_credentials",
              new=AsyncMock(return_value=object())),
        patch("app.channels.lab_search_handler.drive_search_files", side_effect=fake_search),
        patch("app.channels.lab_search_handler._write_audit", new=AsyncMock()),
    ):
        handled = await handle_staff_message(
            TENANT_ID, TENANT_SLUG, "user-1", "chat-1",
            "nombre: Elba Zacarias; tipo: hemograma", send,
        )

    assert handled is True
    assert call_count == 2
    preview_msg = str(send.call_args_list[-1])
    assert "solo por nombre" in preview_msg
    assert "JUN0049-SP-ELBA ZACARIAS.pdf" in preview_msg


@pytest.mark.asyncio
async def test_group_chat_second_user_does_not_inherit_staff_session():
    """Two users in the SAME group chat — only the one who unlocked gets
    staff mode. chat_id must never be the cache key (see module docstring)."""
    row = _secret_row(bound_user_id=None)
    session = _session_returning(row)
    send = AsyncMock()

    with patch("app.channels.lab_search_handler.AsyncSessionLocal", return_value=_ctx(session)):
        handled = await handle_staff_message(
            TENANT_ID, TENANT_SLUG, "user-1", "group-chat-99", RAW_SECRET, send
        )
    assert handled is True
    assert is_staff_session_active(TENANT_SLUG, "user-1") is True
    assert is_staff_session_active(TENANT_SLUG, "user-2") is False

    with patch("app.channels.lab_search_handler.AsyncSessionLocal") as mock_session_local:
        handled_2 = await handle_staff_message(
            TENANT_ID, TENANT_SLUG, "user-2", "group-chat-99", "hola a todos", send
        )
    assert handled_2 is False  # falls through to normal patient routing
    mock_session_local.assert_not_called()
