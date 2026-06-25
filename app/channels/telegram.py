import hmac
import logging

import httpx
from fastapi import APIRouter, Request
from langchain_core.messages import HumanMessage
from sqlalchemy import text

from app.channels.base import ChannelEvent
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])

MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB


async def _send(token: str, chat_id: int | str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
        )
        if r.status_code != 200:
            logger.warning("tg_send_failed chat=%s status=%d", chat_id, r.status_code)


async def _download_file(token: str, file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as c:
        meta = await c.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
        file_path = meta.json()["result"]["file_path"]
        return (await c.get(f"https://api.telegram.org/file/bot{token}/{file_path}")).content


class TelegramAdapter:
    """ChannelAdapter implementation for the Telegram Bot API."""

    channel = "telegram"

    def __init__(self, tenant_slug: str, bot_token: str, webhook_secret: str) -> None:
        self._slug = tenant_slug
        self._token = bot_token
        self._secret = webhook_secret

    async def verify(self, request: Request) -> bool:
        header = request.headers.get("x-telegram-bot-api-secret-token", "")
        return hmac.compare_digest(header, self._secret)

    async def normalize(self, body: dict) -> ChannelEvent | None:
        msg = body.get("message") or body.get("edited_message")
        if not msg or "voice" in msg:
            return None
        text = msg.get("text", "").strip()
        if not text:
            return None
        chat_id = str(msg["chat"]["id"])
        user_id = str((msg.get("from") or {}).get("id", "unknown"))
        return ChannelEvent(
            tenant_slug=self._slug,
            channel=self.channel,
            user_id=user_id,
            chat_id=chat_id,
            text=text,
            thread_id=f"tenant:{self._slug}:user:{user_id}:channel:telegram",
        )

    async def send(self, event: ChannelEvent, text: str) -> None:
        await _send(self._token, event.chat_id, text)


@router.post("/telegram/{tenant_slug}")
async def telegram_webhook(
    tenant_slug: str,
    request: Request,
):
    # Always return 200 — Telegram retries on non-200 causing duplicate processing
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT bot_token, webhook_secret FROM tenants WHERE slug = :s AND active = true"),
            {"s": tenant_slug},
        )).first()

    if not row:
        return {"ok": True}

    adapter = TelegramAdapter(tenant_slug, row.bot_token, row.webhook_secret)

    if not await adapter.verify(request):
        logger.warning("tg_bad_secret tenant=%s", tenant_slug)
        return {"ok": True}

    body = await request.json()
    msg = body.get("message") or body.get("edited_message")
    if not msg:
        return {"ok": True}

    chat_id = msg["chat"]["id"]
    user_id = str((msg.get("from") or {}).get("id", "unknown"))

    if "voice" in msg:
        voice = msg["voice"]
        if voice.get("file_size", 0) > MAX_VOICE_BYTES:
            await _send(row.bot_token, chat_id, "Archivo de voz demasiado grande (máx 10MB).")
            return {"ok": True}
        try:
            audio = await _download_file(row.bot_token, voice["file_id"])
            from app.services.stt import transcribe
            text_content = await transcribe(audio, "voice.ogg")
        except Exception as exc:
            logger.warning("tg_stt_failed user=%s err=%s", user_id, exc)
            return {"ok": True}
        event = ChannelEvent(
            tenant_slug=tenant_slug, channel="telegram",
            user_id=user_id, chat_id=str(chat_id),
            text=text_content,
            thread_id=f"tenant:{tenant_slug}:user:{user_id}:channel:telegram",
        )
    else:
        event = await adapter.normalize(body)
        if not event:
            return {"ok": True}

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"https://api.telegram.org/bot{row.bot_token}/sendChatAction",
                json={"chat_id": event.chat_id, "action": "typing"},
            )
    except Exception:
        pass  # typing indicator is best-effort; never block the response

    try:
        result = await request.app.state.graph.ainvoke(
            {"tenant_id": tenant_slug, "thread_id": event.thread_id,
             "messages": [HumanMessage(content=event.text)], "answer": ""},
            config={"configurable": {"thread_id": event.thread_id}},
        )
        response = result.get("answer") or ""
        if not response and result.get("messages"):
            response = result["messages"][-1].content
        if not response:
            response = "Lo siento, no pude generar una respuesta."
    except Exception:
        logger.exception("tg_graph_failed thread=%s", event.thread_id)
        response = "Lo siento, ocurrió un error. Por favor intenta de nuevo."

    try:
        await adapter.send(event, response)
    except Exception:
        logger.warning("tg_final_send_failed chat=%s", event.chat_id)
    return {"ok": True}
