import base64
import hmac
import logging
import re
from collections import OrderedDict

import httpx
from fastapi import APIRouter, BackgroundTasks, Request
from langchain_core.messages import HumanMessage
from sqlalchemy import text

from app.channels.base import ChannelEvent
from app.config import settings
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["telegram"])

MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB

# Dedup cache: update_id → True. Bounded to 1000 entries (LRU).
_SEEN_UPDATES: OrderedDict[int, bool] = OrderedDict()
_SEEN_MAX = 1000


def _is_duplicate(update_id: int) -> bool:
    if update_id in _SEEN_UPDATES:
        return True
    _SEEN_UPDATES[update_id] = True
    if len(_SEEN_UPDATES) > _SEEN_MAX:
        _SEEN_UPDATES.popitem(last=False)
    return False


async def set_webhook(token: str, webhook_url: str, secret: str) -> bool:
    """Register a webhook with Telegram. Returns True on success."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                f"https://api.telegram.org/bot{token}/setWebhook",
                json={"url": webhook_url, "secret_token": secret[:256]},
            )
        data = r.json()
        ok = r.status_code == 200 and data.get("ok", False)
        if not ok:
            logger.warning("tg_set_webhook_failed token=...%s url=%s err=%s",
                           token[-6:], webhook_url, data.get("description"))
        return ok
    except Exception as exc:
        logger.warning("tg_set_webhook_error: %s", exc)
        return False


async def delete_webhook(token: str) -> None:
    """Unregister the Telegram webhook. Best-effort — errors are logged, not raised."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/deleteWebhook")
        if r.status_code != 200 or not r.json().get("ok"):
            logger.warning("tg_delete_webhook_failed token=...%s", token[-6:])
    except Exception as exc:
        logger.warning("tg_delete_webhook_error: %s", exc)


async def get_webhook_info(token: str) -> dict:
    """Fetch current webhook info from Telegram for status checks (T7)."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}"}
        data = r.json()
        if not data.get("ok"):
            return {"ok": False, "error": data.get("description", "unknown")}
        return {"ok": True, "result": data.get("result", {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _wa_to_tg_html(text: str) -> str:
    """Convert WhatsApp-style markdown to Telegram HTML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\*([^\*\n]+)\*', r'<b>\1</b>', text)
    text = re.sub(r'_([^_\n]+)_', r'<i>\1</i>', text)
    return text


async def _send(token: str, chat_id: int | str, text: str) -> None:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": _wa_to_tg_html(text), "parse_mode": "HTML"},
        )
        if r.status_code != 200:
            logger.warning("tg_send_failed chat=%s status=%d body=%s", chat_id, r.status_code, r.text[:120])


async def _download_file(token: str, file_id: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as c:
        meta = await c.get(f"https://api.telegram.org/bot{token}/getFile?file_id={file_id}")
        file_path = meta.json()["result"]["file_path"]
        return (await c.get(f"https://api.telegram.org/file/bot{token}/{file_path}")).content


_VISION_UNCERTAIN = "__VISION_UNCERTAIN__"

_VISION_EXTRACT_PROMPT = (
    "Analiza esta imagen médica (orden de examen, informe, o solicitud de biopsia). "
    "Transcribe el nombre del procedimiento o examen EXACTAMENTE como aparece escrito en la "
    "imagen — no lo traduzcas a un sinónimo clínico ni asumas qué examen 'parecido' podría ser. "
    "Luego formula una pregunta de precio en español usando ese texto literal, por ejemplo: "
    "'¿Cuánto cuesta un examen de IGRA?' o '¿Cuál es el precio de una resección de tumor de mama?'. "
    f"Si el texto no es legible, está cortado, borroso, o hay varios exámenes distintos y no "
    f"puedes determinar cuál se pregunta, responde ÚNICAMENTE con: {_VISION_UNCERTAIN}\n"
    "En cualquier otro caso responde ÚNICAMENTE con la pregunta, sin explicaciones adicionales."
)


async def _extract_procedure_query(img_bytes: bytes, caption: str) -> str:
    if not settings.openai_vision_model:
        raise RuntimeError("OPENAI_VISION_MODEL not configured")
    prompt = f"{caption}\n\n{_VISION_EXTRACT_PROMPT}" if caption else _VISION_EXTRACT_PROMPT
    img_b64 = base64.b64encode(img_bytes).decode()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={
                "model": settings.openai_vision_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    ],
                }],
            },
        )
    if r.status_code != 200:
        logger.warning("tg_vision_api_failed status=%d body=%s", r.status_code, r.text[:200])
        raise RuntimeError(f"Vision API returned {r.status_code}")
    return r.json()["choices"][0]["message"]["content"].strip()


class TelegramAdapter:
    """ChannelAdapter implementation for the Telegram Bot API."""

    channel = "telegram"

    def __init__(self, tenant_slug: str, bot_token: str, webhook_secret: str) -> None:
        self._slug = tenant_slug
        self._token = bot_token
        self._secret = webhook_secret

    def verify_secret(self, secret_header: str) -> bool:
        return hmac.compare_digest(secret_header, self._secret)

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


async def _process_update(
    tenant_slug: str,
    bot_token: str,
    webhook_secret: str,
    body: dict,
    app_state,
) -> None:
    """Heavy processing: runs AFTER 200 is returned to Telegram."""
    msg = body.get("message") or body.get("edited_message")
    if not msg:
        return

    chat_id = msg["chat"]["id"]
    user_id = str((msg.get("from") or {}).get("id", "unknown"))
    adapter = TelegramAdapter(tenant_slug, bot_token, webhook_secret)

    if "voice" in msg:
        voice = msg["voice"]
        if voice.get("file_size", 0) > MAX_VOICE_BYTES:
            await _send(bot_token, chat_id, "Archivo de voz demasiado grande (máx 10MB).")
            return
        try:
            audio = await _download_file(bot_token, voice["file_id"])
            from app.services.stt import transcribe
            text_content = await transcribe(audio, "voice.ogg")
        except Exception as exc:
            logger.warning("tg_stt_failed user=%s err=%s", user_id, exc)
            return
        if not text_content:
            return
        event = ChannelEvent(
            tenant_slug=tenant_slug, channel="telegram",
            user_id=user_id, chat_id=str(chat_id),
            text=text_content,
            thread_id=f"tenant:{tenant_slug}:user:{user_id}:channel:telegram",
        )
    elif "photo" in msg:
        if not settings.openai_vision_model:
            await _send(bot_token, chat_id, "El análisis de imágenes no está habilitado.")
            return
        photo = msg["photo"][-1]  # largest resolution
        caption = msg.get("caption", "")
        try:
            img_bytes = await _download_file(bot_token, photo["file_id"])
            procedure_query = await _extract_procedure_query(img_bytes, caption)
        except Exception as exc:
            logger.warning("tg_vision_failed user=%s err=%s", user_id, exc)
            await _send(bot_token, chat_id, "No pude procesar la imagen. Por favor intenta de nuevo.")
            return
        if _VISION_UNCERTAIN in procedure_query:
            # Don't guess and forward an uncertain read into the RAG pipeline —
            # a wrong procedure name there looks just like a confident, correct
            # answer downstream. Ask the user to type it instead.
            logger.warning("tg_vision_uncertain tenant=%s user=%s", tenant_slug, user_id)
            await _send(
                bot_token, chat_id,
                "No pude leer con seguridad el examen en la imagen. "
                "¿Puedes escribirme el nombre del examen o procedimiento?",
            )
            return
        logger.warning("tg_vision_extracted tenant=%s query=%s", tenant_slug, procedure_query[:120])
        event = ChannelEvent(
            tenant_slug=tenant_slug, channel="telegram",
            user_id=user_id, chat_id=str(chat_id),
            text=procedure_query,
            thread_id=f"tenant:{tenant_slug}:user:{user_id}:channel:telegram",
        )
    else:
        event = await adapter.normalize(body)
        if not event:
            return

    graph = getattr(app_state, "graph", None)
    if graph is None:
        logger.error("tg_graph_not_initialized thread=%s", event.thread_id)
        try:
            await adapter.send(event, "Lo siento, el servicio no está disponible. Por favor intenta de nuevo más tarde.")
        except Exception:
            pass
        return

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(
                f"https://api.telegram.org/bot{bot_token}/sendChatAction",
                json={"chat_id": event.chat_id, "action": "typing"},
            )
    except Exception:
        pass

    try:
        result = await graph.ainvoke(
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
    except Exception as exc:
        logger.warning("tg_final_send_failed chat=%s type=%s err=%s",
                       event.chat_id, type(response).__name__, exc, exc_info=True)


@router.post("/telegram/{tenant_slug}")
async def telegram_webhook(
    tenant_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
):
    # Always return 200 fast — Telegram retries on timeout causing duplicate processing.
    # All heavy work (LLM) runs in background AFTER this handler returns.
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT bot_token, webhook_secret FROM tenants WHERE slug = :s AND active = true"),
            {"s": tenant_slug},
        )).first()

    if not row:
        return {"ok": True}

    secret_header = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not hmac.compare_digest(secret_header, row.webhook_secret):
        logger.warning("tg_bad_secret tenant=%s", tenant_slug)
        return {"ok": True}

    body = await request.json()

    update_id = body.get("update_id")
    if update_id is not None and _is_duplicate(update_id):
        logger.info("tg_duplicate_update update_id=%s tenant=%s", update_id, tenant_slug)
        return {"ok": True}

    background_tasks.add_task(
        _process_update,
        tenant_slug,
        row.bot_token,
        row.webhook_secret,
        body,
        request.app.state,
    )
    return {"ok": True}
