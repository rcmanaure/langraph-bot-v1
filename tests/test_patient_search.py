"""
Mock-based tests for patient search (T3/T4/T5, plan-eng-review 2026-07-20).

Coverage:
  app.services.patient_search — validation, disambiguation (0/1/N), query
    escaping, confirmed/unconfirmed classification, result_id sign/verify
  GET /operator/patient-search               — missing identity, not found,
    ambiguous, success, blocking audit failure
  GET /operator/patient-search/{id}/download — invalid envelope, cross-tenant,
    success, blocking audit failure (PHI not served if audit fails)
"""
import sys
import types
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

for _mod in ("pypdf", "filetype", "tiktoken"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

import pytest  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.auth import verify_tenant_scoped_key  # noqa: E402
from app.routes.operator import router  # noqa: E402
from app.services import patient_search as ps  # noqa: E402

# ═════════════════════════════════════════════════════════════════════════════
# Service-level unit tests
# ═════════════════════════════════════════════════════════════════════════════

def test_validate_query_missing_name_raises():
    with pytest.raises(ps.SearchValidationError):
        ps.validate_query("", "12345678")


def test_validate_query_missing_dni_raises():
    with pytest.raises(ps.SearchValidationError):
        ps.validate_query("Elba Zacarias", "")


def test_escape_gmail_query_term_quotes_and_strips_quotes():
    assert ps.escape_gmail_query_term('from:evil@x.com') == '"from:evil@x.com"'
    assert ps.escape_gmail_query_term('a"b') == '"ab"'


def test_is_confirmed_true_when_dni_in_text():
    assert ps.is_confirmed("Resultado de Elba Zacarias DNI 12345678", "12345678") is True


def test_is_confirmed_false_when_dni_absent():
    assert ps.is_confirmed("Resultado de Elba Zacarias", "12345678") is False


def test_result_id_round_trip():
    token = ps.build_result_id(1, "drive", file_id="abc")
    envelope = ps.verify_result_id(token)
    assert envelope["tenant_id"] == 1
    assert envelope["source"] == "drive"
    assert envelope["file_id"] == "abc"


def test_result_id_tampered_rejected():
    token = ps.build_result_id(1, "drive", file_id="abc")
    # Middle char, not last — see test_state_tampered_rejected in
    # test_google_oauth.py for why the last base64url char is unsafe to flip.
    mid = len(token) // 2
    replacement = "X" if token[mid] != "X" else "Z"
    tampered = token[:mid] + replacement + token[mid + 1:]
    with pytest.raises(ps.SearchValidationError):
        ps.verify_result_id(tampered)


@pytest.mark.asyncio
async def test_disambiguate_patient_not_found():
    db = AsyncMock()
    result = MagicMock()
    result.fetchall.return_value = []
    db.execute = AsyncMock(return_value=result)
    with pytest.raises(ps.PatientNotFoundError):
        await ps.disambiguate_patient(db, tenant_id=1, name="Ghost", dni_or_dob="000")


@pytest.mark.asyncio
async def test_disambiguate_patient_single_match():
    db = AsyncMock()
    row = MagicMock()
    row._mapping = {"id": uuid.uuid4(), "patient_name": "Elba Zacarias", "dni_or_dob": "12345678"}
    result = MagicMock()
    result.fetchall.return_value = [row]
    db.execute = AsyncMock(return_value=result)
    patient = await ps.disambiguate_patient(db, tenant_id=1, name="Elba Zacarias", dni_or_dob="12345678")
    assert patient["patient_name"] == "Elba Zacarias"


@pytest.mark.asyncio
async def test_disambiguate_patient_ambiguous_raises_with_candidates():
    db = AsyncMock()
    rows = []
    for _ in range(2):
        row = MagicMock()
        row._mapping = {"id": uuid.uuid4(), "patient_name": "Elba Zacarias", "dni_or_dob": "12345678"}
        rows.append(row)
    result = MagicMock()
    result.fetchall.return_value = rows
    db.execute = AsyncMock(return_value=result)
    with pytest.raises(ps.AmbiguousPatientError) as exc:
        await ps.disambiguate_patient(db, tenant_id=1, name="Elba Zacarias", dni_or_dob="12345678")
    assert len(exc.value.candidates) == 2


@pytest.mark.asyncio
async def test_patient_search_classifies_confirmed_and_unconfirmed():
    db = AsyncMock()
    with patch.object(ps, "disambiguate_patient", AsyncMock(return_value={
        "patient_name": "Elba Zacarias", "dni_or_dob": "12345678",
    })), \
         patch.object(ps, "refresh_access_token", return_value=MagicMock()), \
         patch.object(ps, "search_gmail", return_value=[{
             "source": "gmail", "message_id": "m1", "attachment_id": "a1",
             "filename": "JUN0049-SP-ELBA ZACARIAS.pdf",
             "verify_text": "Resultado 12345678 Elba Zacarias",
         }]), \
         patch.object(ps, "search_drive", return_value=[{
             "source": "drive", "file_id": "f1", "filename": "Elba Zacarias otro paciente.pdf",
             "verify_text": "Elba Zacarias otro paciente.pdf",
         }]), \
         patch.object(ps, "google_build", return_value=MagicMock()):
        tenant = MagicMock(id=1, google_refresh_token="refresh-token")
        result = await ps.patient_search(db, tenant, "Elba Zacarias", "12345678")

    statuses = {r["filename"]: r["status"] for r in result["results"]}
    assert statuses["JUN0049-SP-ELBA ZACARIAS.pdf"] == "match_exacto"
    assert statuses["Elba Zacarias otro paciente.pdf"] == "candidato_no_confirmado"


def test_search_gmail_retries_once_on_429_then_succeeds():
    from googleapiclient.errors import HttpError

    rate_limit_resp = MagicMock(status=429)
    rate_limited_error = HttpError(rate_limit_resp, b"rate limited")

    service = MagicMock()
    list_execute = MagicMock(side_effect=[rate_limited_error, {"messages": []}])
    service.users.return_value.messages.return_value.list.return_value.execute = list_execute

    with patch("app.services.llm.time.sleep"):
        results = ps.search_gmail(service, '"Elba Zacarias"')

    assert results == []
    assert list_execute.call_count == 2


def test_search_gmail_malformed_response_raises_search_validation_error():
    service = MagicMock()
    # "messages" key present but each entry missing "id" → KeyError when read
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{}],
    }
    service.users.return_value.messages.return_value.get.return_value.execute.side_effect = KeyError("id")

    with pytest.raises(ps.SearchValidationError):
        ps.search_gmail(service, '"Elba Zacarias"')


@pytest.mark.asyncio
async def test_patient_search_no_google_connection_raises_refresh_error():
    db = AsyncMock()
    with patch.object(ps, "disambiguate_patient", AsyncMock(return_value={"patient_name": "X", "dni_or_dob": "1"})):
        tenant = MagicMock(id=1, google_refresh_token=None)
        with pytest.raises(ps.RefreshError):
            await ps.patient_search(db, tenant, "X", "1")


# ═════════════════════════════════════════════════════════════════════════════
# Route-level tests
# ═════════════════════════════════════════════════════════════════════════════

def make_app():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_tenant_scoped_key] = lambda: "acme"
    return app


async def req(app, method, path, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.request(method.upper(), path, **kwargs)


def _tenant(id=1, slug="acme", refresh_token="tok"):
    t = MagicMock()
    t.id = id
    t.slug = slug
    t.google_refresh_token = refresh_token
    return t


def _scalar_result(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.mark.asyncio
async def test_patient_search_endpoint_missing_identity_header_returns_422():
    app = make_app()
    r = await req(app, "get", "/operator/patient-search", params={"name": "X", "dni_or_dob": "1"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_patient_search_endpoint_tenant_not_found_returns_404():
    app = make_app()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalar_result(None))
    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)):
        r = await req(
            app, "get", "/operator/patient-search",
            params={"name": "X", "dni_or_dob": "1"}, headers={"x-operator-identity": "Dra. Lopez"},
        )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_patient_search_endpoint_success_inserts_audit():
    app = make_app()
    t = _tenant()
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalar_result(t))
    session.commit = AsyncMock()

    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)), \
         patch("app.routes.operator.patient_search_svc.patient_search", AsyncMock(return_value={
             "patient_name": "Elba Zacarias", "results": [],
         })):
        r = await req(
            app, "get", "/operator/patient-search",
            params={"name": "Elba Zacarias", "dni_or_dob": "12345678"},
            headers={"x-operator-identity": "Dra. Lopez"},
        )
    assert r.status_code == 200
    # tenant lookup + audit insert = at least 2 execute calls
    assert session.execute.call_count >= 2
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_patient_search_endpoint_audit_failure_returns_503():
    app = make_app()
    t = _tenant()
    session = AsyncMock()
    session.commit = AsyncMock()

    call_count = {"n": 0}

    async def execute_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _scalar_result(t)
        raise Exception("db down")

    session.execute = AsyncMock(side_effect=execute_side_effect)

    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)), \
         patch("app.routes.operator.patient_search_svc.patient_search", AsyncMock(return_value={
             "patient_name": "Elba Zacarias", "results": [],
         })):
        r = await req(
            app, "get", "/operator/patient-search",
            params={"name": "Elba Zacarias", "dni_or_dob": "12345678"},
            headers={"x-operator-identity": "Dra. Lopez"},
        )
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_download_invalid_result_id_returns_400():
    app = make_app()
    r = await req(app, "get", "/operator/patient-search/not-a-token/download", headers={"x-operator-identity": "Dra. Lopez"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_download_cross_tenant_envelope_rejected_403():
    app = make_app()
    other_tenant_token = ps.build_result_id(999, "drive", file_id="f1")
    t = _tenant(id=1)  # authenticated tenant is id=1, envelope says tenant_id=999
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalar_result(t))

    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)):
        r = await req(
            app, "get", f"/operator/patient-search/{other_tenant_token}/download",
            headers={"x-operator-identity": "Dra. Lopez"},
        )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_download_success_returns_bytes_and_inserts_audit():
    app = make_app()
    token = ps.build_result_id(1, "drive", file_id="f1")
    t = _tenant(id=1)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_scalar_result(t))
    session.commit = AsyncMock()

    drive_service = MagicMock()
    drive_service.files.return_value.get_media.return_value.execute.return_value = b"%PDF-1.4 fake pdf bytes"

    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)), \
         patch("app.routes.operator.google_oauth.refresh_access_token", return_value=MagicMock()), \
         patch("app.routes.operator.google_build", return_value=drive_service):
        r = await req(
            app, "get", f"/operator/patient-search/{token}/download",
            headers={"x-operator-identity": "Dra. Lopez"},
        )
    assert r.status_code == 200
    assert r.content == b"%PDF-1.4 fake pdf bytes"
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_download_audit_failure_does_not_serve_phi():
    app = make_app()
    token = ps.build_result_id(1, "drive", file_id="f1")
    t = _tenant(id=1)
    session = AsyncMock()

    call_count = {"n": 0}

    async def execute_side_effect(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _scalar_result(t)
        raise Exception("db down")

    session.execute = AsyncMock(side_effect=execute_side_effect)

    drive_service = MagicMock()
    drive_service.files.return_value.get_media.return_value.execute.return_value = b"%PDF-1.4 fake pdf bytes"

    with patch("app.routes.operator.AsyncSessionLocal", return_value=_ctx(session)), \
         patch("app.routes.operator.google_oauth.refresh_access_token", return_value=MagicMock()), \
         patch("app.routes.operator.google_build", return_value=drive_service):
        r = await req(
            app, "get", f"/operator/patient-search/{token}/download",
            headers={"x-operator-identity": "Dra. Lopez"},
        )
    assert r.status_code == 503
    assert r.content != b"%PDF-1.4 fake pdf bytes"
