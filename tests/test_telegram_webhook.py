"""
Edge-case tests for the Telegram webhook handler.
All DB and HTTP calls are mocked — no live services required.

Known bugs surfaced here (marked with BUG):
  - sendChatAction network error propagates as 500 instead of 200
  - _send() network error propagates as 500 instead of 200
  - Graph returning empty answer + empty messages → sends empty string to user
"""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.channels.telegram import router

SLUG = "demo"
SECRET = "test-secret-123"
BOT = "999:FAKE"


# ── App factory ───────────────────────────────────────────────────────────────

def make_app(graph=None) -> FastAPI:
    """Minimal app with only the Telegram router — no lifespan/DB connection."""
    app = FastAPI()
    app.include_router(router)
    if graph is not None:
        app.state.graph = graph
    return app


# ── Payload builders ──────────────────────────────────────────────────────────

def text_update(text="hola", chat_id=100, user_id=42):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": text}}


def voice_update(file_id="f1", file_size=1024, chat_id=100, user_id=42):
    return {"message": {"chat": {"id": chat_id}, "from": {"id": user_id},
                        "voice": {"file_id": file_id, "file_size": file_size}}}


def edited_update(text="edited text", chat_id=100, user_id=42):
    return {"edited_message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": text}}


# ── Shared fixtures ───────────────────────────────────────────────────────────

@pytest.fixture()
def mock_graph():
    g = AsyncMock()
    g.ainvoke = AsyncMock(return_value={"answer": "Respuesta OK", "messages": []})
    return g


@pytest.fixture()
def db_row():
    row = MagicMock()
    row.webhook_secret = SECRET
    row.bot_token = BOT
    return row


@pytest.fixture()
def mock_db(db_row):
    """Patches AsyncSessionLocal to return a valid tenant row."""
    result = MagicMock()
    result.first.return_value = db_row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.channels.telegram.AsyncSessionLocal", return_value=ctx):
        yield


@pytest.fixture()
def no_tenant():
    """Patches AsyncSessionLocal to return nothing (tenant not found)."""
    result = MagicMock()
    result.first.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.channels.telegram.AsyncSessionLocal", return_value=ctx):
        yield


@pytest.fixture()
def mock_http():
    """Blocks all outbound httpx calls (Telegram API)."""
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
    """Helper: POST to the webhook endpoint."""
    headers = {"x-telegram-bot-api-secret-token": secret} if secret else {}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(f"/webhook/telegram/{SLUG}", json=body, headers=headers)


# ── 1. Tenant resolution ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_slug_returns_ok(no_tenant):
    """Unknown slug → silently returns {"ok": true}. No error to attacker."""
    app = make_app()
    r = await _post(app, text_update())
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_inactive_tenant_returns_ok():
    """Inactive tenant (active=false filtered by SQL) → same as not found."""
    # DB returns None because query has AND active = true
    result = MagicMock()
    result.first.return_value = None
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=None)
    with patch("app.channels.telegram.AsyncSessionLocal", return_value=ctx):
        app = make_app()
        r = await _post(app, text_update())
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── 2. Secret validation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_missing_secret_header_returns_ok(mock_db):
    """No secret header → {"ok": true}, not 401. Telegram retries are safe."""
    app = make_app()
    r = await _post(app, text_update(), secret=None)
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_wrong_secret_returns_ok(mock_db):
    """Wrong secret → {"ok": true}. Timing-safe via hmac.compare_digest."""
    app = make_app()
    r = await _post(app, text_update(), secret="wrong-secret")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_empty_string_secret_returns_ok(mock_db):
    """Empty string secret (not the same as missing header) → rejected."""
    app = make_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.post(
            f"/webhook/telegram/{SLUG}",
            json=text_update(),
            headers={"x-telegram-bot-api-secret-token": ""},
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


# ── 3. Message parsing ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_no_message_key_returns_ok(mock_db, mock_http):
    """Payload with no message/edited_message (e.g. channel post) → ok."""
    app = make_app()
    r = await _post(app, {"update_id": 1, "channel_post": {"text": "hi"}})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_edited_message_is_processed(mock_db, mock_http, mock_graph):
    """edited_message (no message key) should be processed normally."""
    app = make_app(mock_graph)
    r = await _post(app, edited_update("texto editado"))
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    mock_graph.ainvoke.assert_awaited_once()
    call_input = mock_graph.ainvoke.call_args[0][0]
    assert call_input["messages"][0].content == "texto editado"


@pytest.mark.asyncio
async def test_message_takes_priority_over_edited_message(mock_db, mock_http, mock_graph):
    """When both message and edited_message are present, message wins."""
    app = make_app(mock_graph)
    payload = {
        "message": {"chat": {"id": 1}, "from": {"id": 1}, "text": "original"},
        "edited_message": {"chat": {"id": 1}, "from": {"id": 1}, "text": "edited"},
    }
    await _post(app, payload)
    call_input = mock_graph.ainvoke.call_args[0][0]
    assert call_input["messages"][0].content == "original"


@pytest.mark.asyncio
async def test_whitespace_only_text_returns_ok(mock_db, mock_http):
    """Message with only spaces strips to empty → no graph call."""
    app = make_app()
    r = await _post(app, text_update("   "))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_empty_text_returns_ok(mock_db, mock_http):
    """Message with empty string text → no graph call."""
    app = make_app()
    r = await _post(app, text_update(""))
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_missing_from_field_uses_unknown(mock_db, mock_http, mock_graph):
    """Message without 'from' field (can happen in groups) → user_id = 'unknown'."""
    app = make_app(mock_graph)
    payload = {"message": {"chat": {"id": 1}, "text": "hola"}}  # no 'from'
    await _post(app, payload)
    call_cfg = mock_graph.ainvoke.call_args[1]["config"]
    assert ":user:unknown:" in call_cfg["configurable"]["thread_id"]


# ── 4. Thread ID format ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_thread_id_format(mock_db, mock_http, mock_graph):
    """thread_id must be: tenant:{slug}:user:{user_id}:channel:telegram"""
    app = make_app(mock_graph)
    await _post(app, text_update(user_id=99))
    call_cfg = mock_graph.ainvoke.call_args[1]["config"]
    assert call_cfg["configurable"]["thread_id"] == f"tenant:{SLUG}:user:99:channel:telegram"


# ── 5. Graph invocation & response ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_graph_answer_sent_to_user(mock_db, mock_http, mock_graph):
    """Graph answer is forwarded to sendMessage."""
    mock_graph.ainvoke = AsyncMock(return_value={"answer": "Precio: $100", "messages": []})
    app = make_app(mock_graph)
    await _post(app, text_update())
    # Verify _send was called with the answer text
    send_calls = mock_http.post.call_args_list
    send_msg = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert send_msg is not None
    assert "Precio: $100" in str(send_msg)


@pytest.mark.asyncio
async def test_fallback_to_messages_when_answer_empty(mock_db, mock_http, mock_graph):
    """If answer is empty, last message content is used."""
    from langchain_core.messages import AIMessage
    mock_graph.ainvoke = AsyncMock(return_value={
        "answer": "",
        "messages": [AIMessage(content="fallback desde messages")],
    })
    app = make_app(mock_graph)
    await _post(app, text_update())
    send_calls = mock_http.post.call_args_list
    send_msg = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert "fallback desde messages" in str(send_msg)


@pytest.mark.asyncio
async def test_graph_exception_sends_spanish_error(mock_db, mock_http, mock_graph):
    """Graph crash → user receives Spanish apology, NOT a 500."""
    mock_graph.ainvoke = AsyncMock(side_effect=RuntimeError("LLM timeout"))
    app = make_app(mock_graph)
    r = await _post(app, text_update())
    assert r.status_code == 200
    send_calls = mock_http.post.call_args_list
    error_call = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert "Lo siento" in str(error_call)


@pytest.mark.asyncio
async def test_empty_answer_and_empty_messages_sends_fallback(mock_db, mock_http, mock_graph):
    """Graph returning empty answer + empty messages → sends a fallback message (not empty string)."""
    mock_graph.ainvoke = AsyncMock(return_value={"answer": "", "messages": []})
    app = make_app(mock_graph)
    r = await _post(app, text_update())
    assert r.status_code == 200
    send_calls = mock_http.post.call_args_list
    send_msg = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert send_msg is not None
    sent_text = send_msg[1]["json"]["text"]
    assert sent_text != ""  # Never sends empty string to Telegram
    assert "Lo siento" in sent_text


# ── 6. Voice messages ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_voice_over_10mb_sends_size_error(mock_db, mock_http, mock_graph):
    """Voice > 10 MB → sends size error, no graph call."""
    app = make_app(mock_graph)
    payload = voice_update(file_size=11 * 1024 * 1024)
    r = await _post(app, payload)
    assert r.status_code == 200
    mock_graph.ainvoke.assert_not_awaited()
    send_calls = mock_http.post.call_args_list
    error_call = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert "10MB" in str(error_call)


@pytest.mark.asyncio
async def test_voice_exactly_at_limit_is_processed(mock_db, mock_http, mock_graph):
    """Voice at exactly 10 MB (not over) → STT proceeds."""
    with patch("app.channels.telegram._download_file", new_callable=AsyncMock,
               return_value=b"audio"), \
         patch("app.services.stt.transcribe", new_callable=AsyncMock,
               return_value="texto transcrito"):
        app = make_app(mock_graph)
        payload = voice_update(file_size=10 * 1024 * 1024)
        r = await _post(app, payload)
    assert r.status_code == 200
    mock_graph.ainvoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_voice_stt_failure_returns_ok(mock_db, mock_http):
    """STT failure → swallowed, returns ok (no graph call, no error to user)."""
    app = make_app()
    with patch("app.channels.telegram._download_file",
               new_callable=AsyncMock, return_value=b"audio"), \
         patch("app.services.stt.transcribe",
               new_callable=AsyncMock, side_effect=Exception("whisper down")):
        r = await _post(app, voice_update())
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_voice_missing_file_size_treated_as_zero(mock_db, mock_http, mock_graph):
    """Voice with no file_size key defaults to 0 → not rejected as too large."""
    with patch("app.channels.telegram._download_file",
               new_callable=AsyncMock, return_value=b"audio"), \
         patch("app.services.stt.transcribe",
               new_callable=AsyncMock, return_value="hola"):
        app = make_app(mock_graph)
        payload = {"message": {
            "chat": {"id": 1}, "from": {"id": 1},
            "voice": {"file_id": "abc"},  # no file_size key
        }}
        r = await _post(app, payload)
    assert r.status_code == 200
    mock_graph.ainvoke.assert_awaited_once()


# ── 7. Network failure edge cases (BUGs) ──────────────────────────────────────

@pytest.mark.asyncio
async def test_send_chat_action_timeout_still_returns_ok(mock_db, mock_graph):
    """sendChatAction timeout is swallowed — handler always returns {"ok": True}."""
    failing = AsyncMock()
    failing.__aenter__ = AsyncMock(return_value=failing)
    failing.__aexit__ = AsyncMock(return_value=None)
    failing.post = AsyncMock(side_effect=httpx.ConnectTimeout("timed out"))

    with patch("app.channels.telegram.httpx.AsyncClient", return_value=failing):
        app = make_app(mock_graph)
        r = await _post(app, text_update())
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_final_send_failure_still_returns_ok(mock_db, mock_graph):
    """_send() network failure is logged but swallowed — Telegram always gets 200."""
    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:  # sendChatAction — succeeds
            r = MagicMock()
            r.status_code = 200
            return r
        raise httpx.ConnectTimeout("sendMessage failed")

    client_mock = AsyncMock()
    client_mock.__aenter__ = AsyncMock(return_value=client_mock)
    client_mock.__aexit__ = AsyncMock(return_value=None)
    client_mock.post = AsyncMock(side_effect=side_effect)

    with patch("app.channels.telegram.httpx.AsyncClient", return_value=client_mock):
        app = make_app(mock_graph)
        r = await _post(app, text_update())
    assert r.status_code == 200
    assert r.json() == {"ok": True}


@pytest.mark.asyncio
async def test_voice_over_10mb_no_graph_in_app_state(mock_db, mock_http):
    """Voice > 10 MB returns early before graph access — app.state.graph not required."""
    app = make_app()  # no graph set — proves lazy access path works
    payload = voice_update(file_size=11 * 1024 * 1024)
    r = await _post(app, payload)
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    send_calls = mock_http.post.call_args_list
    error_call = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert error_call is not None and "10MB" in str(error_call)


@pytest.mark.asyncio
async def test_graph_not_initialized_sends_service_unavailable(mock_db, mock_http):
    """Normal text message with no graph set → user-facing error, no AttributeError crash."""
    app = make_app()  # no graph set
    r = await _post(app, text_update())
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    send_calls = mock_http.post.call_args_list
    error_call = next((c for c in send_calls if "sendMessage" in str(c)), None)
    assert error_call is not None, "Expected a user-facing error message when graph not initialized"
    assert "Lo siento" in str(error_call) or "no está disponible" in str(error_call)


@pytest.mark.asyncio
async def test_duplicate_update_id_not_processed_twice(mock_db, mock_http, mock_graph):
    """Same update_id sent twice → graph invoked exactly once (dedup)."""
    app = make_app(mock_graph)
    payload = {"update_id": 9999, **text_update()}
    await _post(app, payload)
    await _post(app, payload)
    mock_graph.ainvoke.assert_awaited_once()
