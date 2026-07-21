"""
Mock-based tests for Google OAuth (T2, plan-eng-review 2026-07-20).

Coverage:
  app.services.google_oauth.build_state/verify_state — valid, tampered, expired, reused
  app.crypto.encrypt_value                            — fail-fast when FERNET_KEY missing
  GET /admin/google/connect/{slug}                    — 404, success
  GET /admin/google/callback                          — invalid state, bad code, success
"""
import sys
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

for _mod in ("pypdf", "filetype", "tiktoken"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import jwt  # noqa: E402
import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app import crypto  # noqa: E402
from app.auth import verify_operator_key  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import google_oauth  # noqa: E402


def make_app():
    from app.routes.admin import router
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_operator_key] = lambda: None
    return app


async def req(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.request(method.upper(), path, **kwargs)


def _tenant(id=1, slug="acme"):
    t = MagicMock()
    t.id = id
    t.slug = slug
    return t


def _orm_result(row):
    r = MagicMock()
    r.scalar_one_or_none.return_value = row
    return r


def _session(execute_result):
    s = AsyncMock()
    s.execute = AsyncMock(return_value=execute_result)
    s.commit = AsyncMock()
    return s


def _ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


# ═════════════════════════════════════════════════════════════════════════════
# build_state / verify_state
# ═════════════════════════════════════════════════════════════════════════════

def test_state_round_trip_valid():
    google_oauth._SEEN_STATE_NONCES.clear()
    state = google_oauth.build_state("acme")
    assert google_oauth.verify_state(state) == "acme"


def test_state_tampered_rejected():
    google_oauth._SEEN_STATE_NONCES.clear()
    state = google_oauth.build_state("acme")
    # Flip a char in the middle of the signature segment, not the last char —
    # a 32-byte HS256 signature's final base64url char carries only 4
    # significant bits, so some adjacent-char swaps there don't actually
    # change the decoded bytes (flaky). Middle of the string is safe.
    mid = len(state) // 2
    replacement = "X" if state[mid] != "X" else "Z"
    tampered = state[:mid] + replacement + state[mid + 1:]
    with pytest.raises(google_oauth.OAuthError):
        google_oauth.verify_state(tampered)


def test_state_expired_rejected():
    google_oauth._SEEN_STATE_NONCES.clear()
    payload = {"tenant_slug": "acme", "nonce": "abc123", "exp": int(time.time()) - 10}
    expired_state = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    with pytest.raises(google_oauth.OAuthError):
        google_oauth.verify_state(expired_state)


def test_state_reused_rejected():
    google_oauth._SEEN_STATE_NONCES.clear()
    state = google_oauth.build_state("acme")
    assert google_oauth.verify_state(state) == "acme"
    with pytest.raises(google_oauth.OAuthError):
        google_oauth.verify_state(state)


# ═════════════════════════════════════════════════════════════════════════════
# refresh_access_token — transient retry vs revoked (not retried)
# ═════════════════════════════════════════════════════════════════════════════

def test_refresh_access_token_retries_transient_error_then_succeeds():
    from google.auth.exceptions import TransportError

    with patch("app.services.google_oauth.Credentials") as mock_creds_cls, \
         patch("app.services.llm.time.sleep"):
        mock_creds = MagicMock()
        mock_creds.refresh = MagicMock(side_effect=[TransportError("timeout"), None])
        mock_creds_cls.return_value = mock_creds

        result = google_oauth.refresh_access_token("some-refresh-token")

    assert result is mock_creds
    assert mock_creds.refresh.call_count == 2


def test_refresh_access_token_revoked_raises_refresh_error_no_retry():
    from google.auth.exceptions import RefreshError as GoogleRefreshError

    with patch("app.services.google_oauth.Credentials") as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.refresh = MagicMock(side_effect=GoogleRefreshError("revoked"))
        mock_creds_cls.return_value = mock_creds

        with pytest.raises(google_oauth.RefreshError):
            google_oauth.refresh_access_token("some-refresh-token")

    assert mock_creds.refresh.call_count == 1


# ═════════════════════════════════════════════════════════════════════════════
# crypto.encrypt_value fail-fast
# ═════════════════════════════════════════════════════════════════════════════

def test_encrypt_value_raises_without_fernet_key():
    with patch.object(settings, "fernet_key", ""):
        with pytest.raises(RuntimeError):
            crypto.encrypt_value("some-secret-refresh-token")


def test_encrypt_decrypt_round_trip_with_fernet_key():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    with patch.object(settings, "fernet_key", key):
        encrypted = crypto.encrypt_value("some-secret-refresh-token")
        assert encrypted != "some-secret-refresh-token"
        assert crypto.decrypt_value(encrypted) == "some-secret-refresh-token"


# ═════════════════════════════════════════════════════════════════════════════
# GET /admin/google/connect/{slug}
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_google_connect_not_found_returns_404():
    app = make_app()
    with patch("app.routes.admin.AsyncSessionLocal", return_value=_ctx(_session(_orm_result(None)))):
        r = await req(app, "get", "/admin/google/connect/ghost")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_google_connect_returns_authorization_url():
    app = make_app()
    with patch("app.routes.admin.AsyncSessionLocal", return_value=_ctx(_session(_orm_result(1)))), \
         patch("app.routes.admin.google_oauth.get_authorization_url", return_value="https://accounts.google.com/o/oauth2/auth?state=xyz"):
        r = await req(app, "get", "/admin/google/connect/acme")
    assert r.status_code == 200
    assert r.json()["authorization_url"].startswith("https://accounts.google.com")


# ═════════════════════════════════════════════════════════════════════════════
# GET /admin/google/callback
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_google_callback_invalid_state_returns_400():
    app = make_app()
    r = await req(app, "get", "/admin/google/callback", params={"code": "x", "state": "not-a-real-jwt"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_google_callback_bad_code_returns_400():
    app = make_app()
    google_oauth._SEEN_STATE_NONCES.clear()
    state = google_oauth.build_state("acme")
    with patch("app.routes.admin.google_oauth.exchange_code", side_effect=google_oauth.OAuthError("invalid_grant")):
        r = await req(app, "get", "/admin/google/callback", params={"code": "bad-code", "state": state})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_google_callback_success_stores_refresh_token():
    app = make_app()
    google_oauth._SEEN_STATE_NONCES.clear()
    state = google_oauth.build_state("acme")
    t = _tenant(slug="acme")
    creds = MagicMock(refresh_token="the-refresh-token")

    with patch("app.routes.admin.google_oauth.exchange_code", return_value=creds), \
         patch("app.routes.admin.google_oauth.get_verified_email", return_value="lab@example.com"), \
         patch("app.routes.admin.AsyncSessionLocal", return_value=_ctx(_session(_orm_result(t)))):
        r = await req(app, "get", "/admin/google/callback", params={"code": "good-code", "state": state})

    assert r.status_code == 200
    assert r.json()["google_connected_email"] == "lab@example.com"
    assert t.google_refresh_token == "the-refresh-token"
    assert t.google_connected_email == "lab@example.com"
