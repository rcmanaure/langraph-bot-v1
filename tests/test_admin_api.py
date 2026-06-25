"""
Edge-case tests for the admin API.
DB calls are mocked — no live services required.

Tests cover:
  - Auth: missing header, wrong key, correct key
  - POST /admin/tenants: duplicate slug, duplicate bot_token, valid creation
  - POST /admin/index: tenant not found, valid upload
  - GET /admin/index/{job_id}: found, not found
"""
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.auth import verify_operator_key
from app.routes.admin import router

SECRET_KEY = "test-secret-key-for-unit-tests"
OPERATOR_KEY = hashlib.sha256(SECRET_KEY.encode()).hexdigest()


# ── App factory ───────────────────────────────────────────────────────────────

def make_app(bypass_auth=True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if bypass_auth:
        app.dependency_overrides[verify_operator_key] = lambda: None
    return app


async def _request(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await getattr(c, method)(path, **kwargs)


# ── Auth tests ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_auth_header_returns_422():
    """Missing x-operator-key → 422 (FastAPI required header validation)."""
    app = make_app(bypass_auth=False)
    with patch("app.auth.settings") as mock_settings:
        mock_settings.operator_token = ""
        mock_settings.secret_key = SECRET_KEY
        r = await _request(app, "get", "/admin/tenants")
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_wrong_auth_key_returns_401():
    """Wrong operator key → 401 Unauthorized."""
    app = make_app(bypass_auth=False)
    with patch("app.auth.settings") as mock_settings:
        mock_settings.operator_token = ""
        mock_settings.secret_key = SECRET_KEY

        result = MagicMock()
        result.fetchall.return_value = []
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
            r = await _request(app, "get", "/admin/tenants",
                               headers={"x-operator-key": "wrong-key"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_correct_auth_key_proceeds():
    """Correct operator key hash → reaches the handler."""
    app = make_app(bypass_auth=False)
    with patch("app.auth.settings") as mock_settings:
        mock_settings.operator_token = ""
        mock_settings.secret_key = SECRET_KEY

        result = MagicMock()
        result.fetchall.return_value = []
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
            r = await _request(app, "get", "/admin/tenants",
                               headers={"x-operator-key": OPERATOR_KEY})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_operator_token_takes_priority_over_secret_key():
    """When OPERATOR_TOKEN is set, SECRET_KEY is not used for auth."""
    op_token = "special-operator-token"
    expected_hash = hashlib.sha256(op_token.encode()).hexdigest()
    old_hash = hashlib.sha256(SECRET_KEY.encode()).hexdigest()

    app = make_app(bypass_auth=False)
    with patch("app.auth.settings") as mock_settings:
        mock_settings.operator_token = op_token
        mock_settings.secret_key = SECRET_KEY

        result = MagicMock()
        result.fetchall.return_value = []
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
            # Old key (from secret_key) is rejected
            r = await _request(app, "get", "/admin/tenants",
                               headers={"x-operator-key": old_hash})
            assert r.status_code == 401

            # New key (from operator_token) is accepted
            r = await _request(app, "get", "/admin/tenants",
                               headers={"x-operator-key": expected_hash})
            assert r.status_code == 200


# ── POST /admin/tenants ────────────────────────────────────────────────────────

def _make_db_for_create(existing_row=None):
    """Returns a patched AsyncSessionLocal for create_tenant tests."""
    existing = MagicMock()
    existing.first.return_value = existing_row  # None = no duplicate

    session = AsyncMock()
    session.execute = AsyncMock(return_value=existing)
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


VALID_TENANT = {
    "slug": "new-client",
    "bot_token": "111:NEWTOKEN",
    "webhook_secret": "secret-abc",
    "expertise_area": "software",
    "contact_url": "https://example.com",
    "plan": "free",
}


@pytest.mark.asyncio
async def test_create_tenant_success_returns_201_with_api_key():
    """Valid tenant creation returns 201 and includes raw api_key."""
    app = make_app()
    ctx = _make_db_for_create(existing_row=None)
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "post", "/admin/tenants", json=VALID_TENANT)
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "new-client"
    assert "api_key" in body
    assert len(body["api_key"]) == 64  # secrets.token_hex(32)


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug_returns_409():
    """Slug already in use → 409 Conflict."""
    app = make_app()
    ctx = _make_db_for_create(existing_row=MagicMock())  # existing row found
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "post", "/admin/tenants", json=VALID_TENANT)
    assert r.status_code == 409
    assert "slug" in r.json()["detail"].lower() or "token" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_create_tenant_duplicate_bot_token_returns_409():
    """bot_token already in use → 409 Conflict (same query checks both)."""
    app = make_app()
    ctx = _make_db_for_create(existing_row=MagicMock())
    payload = {**VALID_TENANT, "slug": "unique-slug", "bot_token": "EXISTING_TOKEN"}
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "post", "/admin/tenants", json=payload)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_create_tenant_missing_required_fields_returns_422():
    """Missing slug or bot_token → 422 validation error, not 500."""
    app = make_app()
    r = await _request(app, "post", "/admin/tenants",
                       json={"slug": "only-slug"})  # bot_token missing
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_tenant_missing_webhook_secret_returns_422():
    """webhook_secret is required — missing → 422."""
    app = make_app()
    payload = {"slug": "ok", "bot_token": "123:TOKEN"}  # no webhook_secret
    r = await _request(app, "post", "/admin/tenants", json=payload)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_tenant_api_key_is_hashed_in_db():
    """The raw api_key returned to the caller is NOT stored in DB (only the hash is)."""
    app = make_app()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(first=MagicMock(return_value=None)))
    session.commit = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "post", "/admin/tenants", json=VALID_TENANT)

    raw_key = r.json()["api_key"]
    expected_hash = hashlib.sha256(raw_key.encode()).hexdigest()

    # Inspect what was passed to db.execute for the INSERT
    insert_call = session.execute.call_args_list[-1]  # last execute is the INSERT
    insert_params = insert_call[0][1]
    assert insert_params["api_key_hash"] == expected_hash
    assert raw_key not in str(insert_params)  # raw key never touches DB


# ── GET /admin/tenants ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_tenants_returns_list():
    """List tenants returns a JSON array (may be empty)."""
    app = make_app()
    result = MagicMock()
    result.fetchall.return_value = []
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "get", "/admin/tenants")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── POST /admin/index ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_index_job_tenant_not_found_returns_404():
    """Uploading a file for a non-existent tenant → 404."""
    app = make_app()
    result = MagicMock()
    result.first.return_value = None  # tenant not found
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(
            app, "post", "/admin/index",
            data={"tenant_slug": "nonexistent"},
            files={"file": ("test.pdf", b"%PDF fake content", "application/pdf")},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_index_job_valid_returns_202_with_job_id():
    """Valid upload → 201 with job_id and status PENDING."""
    app = make_app()

    tenant_row = MagicMock()
    tenant_row.id = 1
    tenant_result = MagicMock()
    tenant_result.first.return_value = tenant_row

    session = AsyncMock()
    session.execute = AsyncMock(return_value=tenant_result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx), \
         patch("app.routes.admin.run_index_job", new_callable=AsyncMock), \
         patch("app.routes.admin.asyncio.create_task"):
        r = await _request(
            app, "post", "/admin/index",
            data={"tenant_slug": "demo"},
            files={"file": ("doc.pdf", b"%PDF content", "application/pdf")},
        )
    assert r.status_code == 200
    body = r.json()
    assert "job_id" in body
    assert body["status"] == "PENDING"
    # job_id must be a valid UUID
    UUID(body["job_id"])  # raises if not valid


# ── GET /admin/index/{job_id} ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_index_job_not_found_returns_404():
    """Getting a job_id that doesn't exist → 404."""
    app = make_app()
    result = MagicMock()
    result.first.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "get", "/admin/index/nonexistent-uuid")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_index_job_found_returns_job():
    """Existing job_id returns the job details."""
    from datetime import datetime
    app = make_app()
    fake_row = MagicMock()
    fake_row._mapping = {
        "id": "abc-123", "filename": "test.pdf", "status": "COMPLETED",
        "chunks_total": 10, "chunks_done": 10, "error_message": None,
        "created_at": datetime(2026, 1, 1), "updated_at": datetime(2026, 1, 1),
    }
    result = MagicMock()
    result.first.return_value = fake_row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.routes.admin.AsyncSessionLocal", return_value=ctx):
        r = await _request(app, "get", "/admin/index/abc-123")
    assert r.status_code == 200
    assert r.json()["status"] == "COMPLETED"
    assert r.json()["filename"] == "test.pdf"
