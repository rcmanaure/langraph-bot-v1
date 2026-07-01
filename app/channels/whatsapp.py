import hashlib
import hmac
import json
import logging
from collections import OrderedDict

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, Query, Request
from fastapi.responses import PlainTextResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import text

from app.channels.base import ChannelEvent
from app.crypto import decrypt_value
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["whatsapp"])

_WA = "https://graph.facebook.com/v20.0"

# Dedup cache: wamid → True. Bounded to 1000 entries (LRU).
_SEEN_WA: OrderedDict[str, bool] = OrderedDict()
_SEEN_WA_MAX = 1000


def _is_duplicate_wa(msg_id: str) -> bool:
    if msg_id in _SEEN_WA:
        return True
    _SEEN_WA[msg_id] = True
    if len(_SEEN_WA) > _SEEN_WA_MAX:
        _SEEN_WA.popitem(last=False)
    return False


class WhatsAppAdapter:
    """ChannelAdapter implementation for the WhatsApp Cloud API.

    ponytail: handler below still uses direct calls; migrate when adding a 3rd channel.
    """

    channel = "whatsapp"

    def __init__(
        self,
        tenant_slug: str,
        phone_number_id: str | None,
        access_token: str | None,
        app_secret: str | None,
    ) -> None:
        self._slug = tenant_slug
        self._phone_id = phone_number_id
        self._token = access_token
        self._secret = app_secret

    async def verify(self, request: Request) -> bool:
        if not self._secret:
            return True  # no app_secret configured → allow (dev/permissive mode)
        body_bytes = await request.body()
        sig = request.headers.get("x-hub-signature-256", "").removeprefix("sha256=")
        mac = hmac.new(self._secret.encode(), body_bytes, hashlib.sha256)
        return hmac.compare_digest(sig, mac.hexdigest())

    async def normalize(self, body: dict) -> ChannelEvent | None:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                if change.get("field") != "messages":
                    continue
                for msg in change.get("value", {}).get("messages", []):
                    if msg.get("type") == "text":
                        from_id = msg["from"]
                        return ChannelEvent(
                            tenant_slug=self._slug,
                            channel=self.channel,
                            user_id=from_id,
                            chat_id=from_id,
                            text=msg["text"]["body"],
                            thread_id=f"tenant:{self._slug}:user:{from_id}:channel:whatsapp",
                        )
        return None

    async def send(self, event: ChannelEvent, text: str) -> None:
        if self._token and self._phone_id:
            await _send(self._phone_id, self._token, event.chat_id, text)


async def _send(phone_number_id: str, token: str, to: str, body: str) -> None:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(
            f"{_WA}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={"messaging_product": "whatsapp", "to": to,
                  "type": "text", "text": {"body": body}},
        )
        if r.status_code != 200:
            logger.warning("wa_send_failed to=%s status=%d body=%s", to, r.status_code, r.text[:80])


async def _download_media(media_id: str, token: str) -> bytes:
    async with httpx.AsyncClient(timeout=30) as c:
        url = (await c.get(f"{_WA}/{media_id}",
                           headers={"Authorization": f"Bearer {token}"})).json()["url"]
        return (await c.get(url, headers={"Authorization": f"Bearer {token}"})).content


@router.get("/whatsapp/{tenant_slug}")
async def whatsapp_verify(
    tenant_slug: str,
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT wa_verify_token FROM tenants WHERE slug = :s AND active = true"),
            {"s": tenant_slug},
        )).first()

    if not row or hub_mode != "subscribe" or not hmac.compare_digest(hub_verify_token or "", row.wa_verify_token or ""):
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(hub_challenge or "")


@router.post("/whatsapp/{tenant_slug}")
async def whatsapp_webhook(
    tenant_slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str | None = Header(None),
):
    # Always return 200 fast — Meta retries on timeout, causing duplicate processing.
    # All heavy work (LLM) runs in background AFTER this handler returns.
    body_bytes = await request.body()

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("""
                SELECT wa_phone_number_id, _wa_access_token, _wa_app_secret
                  FROM tenants WHERE slug = :s AND active = true
            """),
            {"s": tenant_slug},
        )).first()

    if not row:
        return {"ok": True}

    # Decrypt secrets (no-op if FERNET_KEY not set)
    try:
        access_token = decrypt_value(row._wa_access_token) if row._wa_access_token else None
        app_secret = decrypt_value(row._wa_app_secret) if row._wa_app_secret else None
    except Exception as exc:
        logger.error("wa_decrypt_failed tenant=%s err=%s", tenant_slug, exc)
        access_token = row._wa_access_token
        app_secret = row._wa_app_secret

    # HMAC verification — when app_secret is configured, a missing header is rejected
    if app_secret:
        if not x_hub_signature_256:
            logger.warning("wa_missing_hmac tenant=%s", tenant_slug)
            return {"ok": True}
        sig = x_hub_signature_256.removeprefix("sha256=")
        mac = hmac.new(app_secret.encode(), body_bytes, hashlib.sha256)
        if not hmac.compare_digest(sig, mac.hexdigest()):
            logger.warning("wa_bad_hmac tenant=%s", tenant_slug)
            return {"ok": True}

    try:
        payload = json.loads(body_bytes)
    except json.JSONDecodeError:
        logger.warning("wa_invalid_json tenant=%s", tenant_slug)
        return {"ok": True}
    if payload.get("object") != "whatsapp_business_account":
        return {"ok": True}

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            for msg in change.get("value", {}).get("messages", []):
                msg_id = msg.get("id", "")
                if msg_id and _is_duplicate_wa(msg_id):
                    logger.info("wa_duplicate_msg wamid=%s tenant=%s", msg_id, tenant_slug)
                    continue
                background_tasks.add_task(
                    _handle_message,
                    request=request,
                    tenant_slug=tenant_slug,
                    phone_number_id=row.wa_phone_number_id,
                    access_token=access_token,
                    msg=msg,
                )

    return {"ok": True}


async def _handle_message(
    request: Request,
    tenant_slug: str,
    phone_number_id: str | None,
    access_token: str | None,
    msg: dict,
) -> None:
    from_id = msg.get("from")
    if not from_id:
        logger.warning("wa_missing_from tenant=%s", tenant_slug)
        return
    msg_type = msg.get("type", "")

    # Update WhatsApp 24h service window
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    INSERT INTO wa_service_windows (tenant_id, user_id, last_user_message_at)
                    SELECT t.id, :uid, now() FROM tenants t WHERE t.slug = :slug
                    ON CONFLICT (tenant_id, user_id) DO UPDATE SET last_user_message_at = now()
                """),
                {"uid": from_id, "slug": tenant_slug},
            )
            await db.commit()
    except Exception as exc:
        logger.warning("wa_service_window_update_failed err=%s", exc)

    if msg_type == "text":
        text_content = msg["text"]["body"]
    elif msg_type in ("audio", "voice"):
        if not access_token:
            return
        try:
            audio = await _download_media(msg[msg_type]["id"], access_token)
            from app.services.stt import transcribe
            text_content = await transcribe(audio, "audio.ogg")
        except Exception as exc:
            logger.warning("wa_stt_failed from=%s err=%s", from_id, exc)
            return
    else:
        logger.debug("wa_unsupported_type type=%s from=%s", msg_type, from_id)
        return

    if not text_content:
        return

    thread_id = f"tenant:{tenant_slug}:user:{from_id}:channel:whatsapp"
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        logger.error("wa_graph_not_initialized thread=%s", thread_id)
        if access_token and phone_number_id:
            await _send(phone_number_id, access_token, from_id, "Lo siento, el servicio no está disponible. Por favor intenta de nuevo más tarde.")
        return
    try:
        result = await graph.ainvoke(
            {"tenant_id": tenant_slug, "thread_id": thread_id,
             "messages": [HumanMessage(content=text_content)], "answer": ""},
            config={"configurable": {"thread_id": thread_id}},
        )
        response = result.get("answer") or ""
        if not response and result.get("messages"):
            response = result["messages"][-1].content
    except Exception:
        logger.exception("wa_graph_failed thread=%s", thread_id)
        response = "Lo siento, ocurrió un error."

    if response and access_token and phone_number_id:
        await _send(phone_number_id, access_token, from_id, response)
