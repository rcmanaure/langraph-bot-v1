"""
Mock-based tests for tenant-scoped operator auth (T0a, plan-eng-review 2026-07-20).

Coverage:
  app.auth.verify_tenant_scoped_key       — 3 paths (valid, bad key, inactive tenant)
  POST /operator/resume/{thread_id}       — cross-tenant thread rejection
  GET  /operator/pending                  — tenant-filtered query
"""
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

# Stub heavy optional modules that the import chain drags in but tests don't use.
# NOTE: unlike test_tenant_crud.py, this module imports app.routes.operator,
# whose chain (app.graph.thread -> app.state) needs the *real* langgraph.graph
# package (AgentState uses langgraph.graph.message.add_messages) — stubbing
# langgraph/langgraph.graph here would shadow that and break the import.
for _mod in ("pypdf", "filetype", "tiktoken", "langchain_openai"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.auth import verify_tenant_scoped_key  # noqa: E402
from app.routes.operator import router  # noqa: E402


def _session(execute_result):
    s = AsyncMock()
    s.execute = AsyncMock(return_value=execute_result)
    return s


def _ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


def _row(slug):
    r = MagicMock()
    r.first.return_value = MagicMock(slug=slug) if slug else None
    return r


def make_app():
    app = FastAPI()
    app.include_router(router)
    return app


async def req(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.request(method.upper(), path, **kwargs)


# ═════════════════════════════════════════════════════════════════════════════
# verify_tenant_scoped_key — direct unit tests
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_verify_tenant_scoped_key_valid_returns_slug():
    with patch("app.auth.AsyncSessionLocal", return_value=_ctx(_session(_row("acme")))):
        slug = await verify_tenant_scoped_key(x_operator_key="rawkey")
    assert slug == "acme"


@pytest.mark.asyncio
async def test_verify_tenant_scoped_key_bad_key_raises_401():
    from fastapi import HTTPException
    with patch("app.auth.AsyncSessionLocal", return_value=_ctx(_session(_row(None)))):
        with pytest.raises(HTTPException) as exc:
            await verify_tenant_scoped_key(x_operator_key="wrongkey")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_tenant_scoped_key_inactive_tenant_raises_401():
    # The query itself filters `active = true`, so an inactive tenant's key
    # produces no row — same 401 path as a bad key.
    from fastapi import HTTPException
    with patch("app.auth.AsyncSessionLocal", return_value=_ctx(_session(_row(None)))):
        with pytest.raises(HTTPException) as exc:
            await verify_tenant_scoped_key(x_operator_key="key-of-inactive-tenant")
    assert exc.value.status_code == 401


# ═════════════════════════════════════════════════════════════════════════════
# POST /operator/resume/{thread_id} — cross-tenant rejection
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_resume_cross_tenant_thread_rejected_403():
    app = make_app()
    app.dependency_overrides[verify_tenant_scoped_key] = lambda: "tenant-a"
    app.state.graph = MagicMock()
    app.state.graph.ainvoke = AsyncMock()

    thread_id = "tenant:tenant-b:user:123:channel:telegram"
    r = await req(app, "post", f"/operator/resume/{thread_id}", json={"text": "hola"})

    assert r.status_code == 403
    app.state.graph.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_same_tenant_thread_allowed():
    app = make_app()
    app.dependency_overrides[verify_tenant_scoped_key] = lambda: "tenant-a"
    app.state.graph = MagicMock()
    app.state.graph.ainvoke = AsyncMock(return_value={"answer": "listo"})

    thread_id = "tenant:tenant-a:user:123:channel:telegram"
    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(_session(MagicMock()))):
        r = await req(app, "post", f"/operator/resume/{thread_id}", json={"text": "hola"})

    assert r.status_code == 200
    app.state.graph.ainvoke.assert_awaited_once()


# ═════════════════════════════════════════════════════════════════════════════
# GET /operator/pending — tenant-filtered query
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_list_pending_filters_by_tenant_slug():
    app = make_app()
    app.dependency_overrides[verify_tenant_scoped_key] = lambda: "tenant-a"

    rows_result = MagicMock()
    rows_result.fetchall.return_value = []
    session = _session(rows_result)

    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)):
        r = await req(app, "get", "/operator/pending")

    assert r.status_code == 200
    bound_params = session.execute.call_args.args[1]
    assert bound_params == {"slug": "tenant-a"}
