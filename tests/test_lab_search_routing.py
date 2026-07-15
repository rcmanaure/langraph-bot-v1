"""Regression tests for the staff-search interception point in
app/channels/telegram.py: staff-mode must sit ABOVE both the existing
photo→vision routing and the normal triage/StateGraph path, without
regressing either for ordinary patients (the plan's mandatory IRON RULE)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.channels.lab_search_handler import _PENDING, _SESSIONS
from app.channels.telegram import router

SLUG = "sp-labs"
SECRET = "test-secret-123"
BOT = "999:FAKE"
TENANT_ID = 7


@pytest.fixture(autouse=True)
def clear_staff_state():
    _SESSIONS.clear()
    _PENDING.clear()
    yield
    _SESSIONS.clear()
    _PENDING.clear()


@pytest.fixture(autouse=True)
def reset_media_groups():
    from app.channels.telegram import _MEDIA_GROUPS
    _MEDIA_GROUPS.clear()
    yield
    _MEDIA_GROUPS.clear()


def make_app(graph=None) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    if graph is not None:
        app.state.graph = graph
    return app


def text_update(text="hola", chat_id=100, user_id=42):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": text}}


def photo_update(file_id="p1", caption="", chat_id=100, user_id=42):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "caption": caption,
                        "photo": [{"file_id": file_id, "file_size": 1024}]}}


def _ctx(session):
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    return ctx


@pytest.fixture()
def mock_graph():
    g = AsyncMock()
    g.ainvoke = AsyncMock(return_value={"answer": "Respuesta OK", "messages": []})
    return g


@pytest.fixture()
def mock_db():
    """Patches telegram.py's own AsyncSessionLocal to return a valid tenant row."""
    row = MagicMock()
    row.id = TENANT_ID
    row.webhook_secret = SECRET
    row.bot_token = BOT
    result = MagicMock()
    result.first.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    with patch("app.channels.telegram.AsyncSessionLocal", return_value=_ctx(session)):
        yield


@pytest.fixture()
def no_staff_secret_match():
    """Patches lab_search_handler's AsyncSessionLocal so any secret lookup
    misses — simulates a tenant with no staff secrets configured yet, or a
    patient message that happens to be long enough to reach the DB check."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    with patch("app.channels.lab_search_handler.AsyncSessionLocal", return_value=_ctx(session)):
        yield


@pytest.fixture()
def mock_http():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"result": {"file_path": "voice/abc.ogg"}}
    resp.content = b"fake_audio"
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)
    client.get = AsyncMock(return_value=resp)
    with patch("app.channels.telegram.httpx.AsyncClient", return_value=client):
        yield client


async def _post(app, body, secret=SECRET):
    headers = {"x-telegram-bot-api-secret-token": secret} if secret else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(f"/webhook/telegram/{SLUG}", json=body, headers=headers)


# ── Ordinary patient flow must be unaffected ─────────────────────────────────

@pytest.mark.asyncio
async def test_normal_patient_message_unaffected_by_staff_check(
    mock_db, mock_http, mock_graph, no_staff_secret_match
):
    """A long, ordinary patient question (>16 chars, so it does reach the
    staff_secret DB check) must still flow through to the graph normally
    once the secret lookup misses."""
    app = make_app(mock_graph)
    r = await _post(app, text_update("¿Cuánto cuesta un hemograma completo?"))
    assert r.status_code == 200
    mock_graph.ainvoke.assert_awaited_once()
    call_input = mock_graph.ainvoke.call_args[0][0]
    assert call_input["messages"][0].content == "¿Cuánto cuesta un hemograma completo?"


@pytest.mark.asyncio
async def test_short_patient_message_never_touches_staff_secret_db(mock_db, mock_http, mock_graph):
    """Short messages short-circuit before any staff_secret DB lookup —
    proven here by NOT patching lab_search_handler's AsyncSessionLocal at
    all and confirming nothing blows up trying to hit a real DB."""
    app = make_app(mock_graph)
    r = await _post(app, text_update("hola"))
    assert r.status_code == 200
    mock_graph.ainvoke.assert_awaited_once()


# ── Staff photo must not fall into the patient vision pipeline ─────────────

@pytest.mark.asyncio
async def test_active_staff_session_photo_gets_not_supported_not_vision(
    mock_db, mock_http, mock_graph
):
    """Regression: an already-unlocked staff session sending a photo must
    get the "not supported yet" reply, and must NOT be routed into
    extract_procedure_query (the patient vision pipeline) — image-as-trigger
    is phase 2, not built here."""
    import time as time_mod

    from app.channels.lab_search_handler import _StaffSession

    _SESSIONS[(SLUG, "42")] = _StaffSession(secret_id=1, label="Dra. Pérez",
                                             expires_at=time_mod.monotonic() + 1800)

    with patch("app.channels.telegram.settings.openai_vision_model", "vision-model"), \
         patch("app.channels.telegram._extract_procedure_query", new_callable=AsyncMock) as mock_vision:
        app = make_app(mock_graph)
        r = await _post(app, photo_update(user_id=42))

    assert r.status_code == 200
    mock_vision.assert_not_called()
    mock_graph.ainvoke.assert_not_awaited()
    send_calls = mock_http.post.call_args_list
    send_msg = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert send_msg is not None
    assert "no está disponible" in str(send_msg)


@pytest.mark.asyncio
async def test_photo_from_non_staff_user_unaffected(mock_db, mock_http, mock_graph):
    """Regression counterpart: a patient (no active staff session) sending a
    photo still goes through the normal vision pipeline unchanged."""
    with patch("app.channels.telegram.settings.openai_vision_model", "vision-model"), \
         patch("app.channels.telegram._download_file", new_callable=AsyncMock, return_value=b"img"), \
         patch("app.channels.telegram._extract_procedure_query", new_callable=AsyncMock,
               return_value="¿Cuánto cuesta un examen de IGRA?"):
        app = make_app(mock_graph)
        r = await _post(app, photo_update(user_id=999))
    assert r.status_code == 200
    mock_graph.ainvoke.assert_awaited_once()
