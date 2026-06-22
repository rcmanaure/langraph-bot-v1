import hashlib
import hmac
import json
import logging

import httpx
from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import PlainTextResponse
from langchain_core.messages import HumanMessage
from sqlalchemy import text

from app.crypto import decrypt_value
from app.db import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhook", tags=["whatsapp"])

_WA = "https://graph.facebook.com/v20.0"


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

    if not row or hub_mode != "subscribe" or hub_verify_token != row.wa_verify_token:
        return PlainTextResponse("Forbidden", status_code=403)
    return PlainTextResponse(hub_challenge or "")


@router.post("/whatsapp/{tenant_slug}")
async def whatsapp_webhook(
    tenant_slug: str,
    request: Request,
    x_hub_signature_256: str | None = Header(None),
):
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
    except Exception:
        access_token = row._wa_access_token
        app_secret = row._wa_app_secret

    # HMAC verification
    if app_secret and x_hub_signature_256:
        sig = x_hub_signature_256.removeprefix("sha256=")
        mac = hmac.new(app_secret.encode(), body_bytes, hashlib.sha256)
        if not hmac.compare_digest(sig, mac.hexdigest()):
            logger.warning("wa_bad_hmac tenant=%s", tenant_slug)
            return {"ok": True}

    payload = json.loads(body_bytes)
    if payload.get("object") != "whatsapp_business_account":
        return {"ok": True}

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            for msg in change.get("value", {}).get("messages", []):
                await _handle_message(
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
    from_id = msg["from"]
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
    try:
        result = await request.app.state.graph.ainvoke(
            {"tenant_id": tenant_slug, "thread_id": thread_id,
             "messages": [HumanMessage(content=text_content)]},
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
