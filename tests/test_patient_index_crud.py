"""
Mock-based tests for patient_index admin CRUD (T3b, plan-eng-review 2026-07-20).

Coverage:
  POST /admin/tenants/{slug}/patient-index — 404 unknown tenant, success
  GET  /admin/tenants/{slug}/patient-index — returns rows scoped to tenant
"""
import sys
import types
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

for _mod in ("pypdf", "filetype", "tiktoken"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.auth import verify_operator_key  # noqa: E402
from app.routes.admin import router  # noqa: E402


def make_app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_operator_key] = lambda: None
    return app


async def req(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.request(method.upper(), path, **kwargs)


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _raw_result(row_dicts):
    rows = []
    for d in row_dicts:
        row = MagicMock()
        row._mapping = d
        rows.append(row)
    r = MagicMock()
    r.fetchall.return_value = rows
    return r


def _session(execute_result):
    s = AsyncMock()
    s.execute = AsyncMock(return_value=execute_result)
    s.add = MagicMock()
    s.commit = AsyncMock()
    s.refresh = AsyncMock()
    return s


def _ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.mark.asyncio
async def test_create_patient_index_entry_unknown_tenant_returns_404():
    app = make_app()
    with patch("app.routes.admin.AsyncSessionLocal", return_value=_ctx(_session(_scalar_result(None)))):
        r = await req(
            app, "post", "/admin/tenants/ghost/patient-index",
            json={"patient_name": "Elba Zacarias", "dni_or_dob": "12345678"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_patient_index_entry_success():
    app = make_app()
    with patch("app.routes.admin.AsyncSessionLocal", return_value=_ctx(_session(_scalar_result(1)))):
        r = await req(
            app, "post", "/admin/tenants/acme/patient-index",
            json={"patient_name": "Elba Zacarias", "dni_or_dob": "12345678"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["patient_name"] == "Elba Zacarias"
    assert body["dni_or_dob"] == "12345678"
    uuid.UUID(body["id"])  # valid UUID string


@pytest.mark.asyncio
async def test_list_patient_index_entries_returns_rows():
    app = make_app()
    rows = [{
        "id": uuid.uuid4(), "patient_name": "Elba Zacarias", "dni_or_dob": "12345678",
        "created_at": datetime.now(timezone.utc),
    }]
    with patch("app.routes.admin.AsyncSessionLocal", return_value=_ctx(_session(_raw_result(rows)))):
        r = await req(app, "get", "/admin/tenants/acme/patient-index")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["patient_name"] == "Elba Zacarias"
