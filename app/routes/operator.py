import base64
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response
from googleapiclient.discovery import build as google_build
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select, text

from app.auth import verify_tenant_scoped_key
from app.db import AsyncSessionLocal
from app.graph.thread import parse_thread_part
from app.models import Tenant
from app.services import google_oauth
from app.services import patient_search as patient_search_svc
from app.services.security import validate_thread_id
from app.services.vision import MAX_MEDIA_BYTES

_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/operator", tags=["operator"])


async def _insert_patient_search_audit(db, *, tenant_id, operator_identity, action, **fields) -> None:
    """Blocking audit insert (eng-review 2026-07-20, outside-voice finding 6):
    the plan promises (P1, not deferred) that every PHI search/download is
    logged — unlike conversation_audit's best-effort pattern, a failed insert
    here must fail the whole request, never serve PHI without a log row."""
    await db.execute(
        text("""
            INSERT INTO patient_search_audit
                (id, tenant_id, operator_identity, action, query_name,
                 query_dni_or_dob, result_ids_shown, downloaded_id, created_at)
            VALUES
                (:id, :tenant_id, :operator_identity, :action, :query_name,
                 :query_dni_or_dob, :result_ids_shown, :downloaded_id, :now)
        """),
        {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "operator_identity": operator_identity,
            "action": action,
            "query_name": fields.get("query_name"),
            "query_dni_or_dob": fields.get("query_dni_or_dob"),
            "result_ids_shown": fields.get("result_ids_shown"),
            "downloaded_id": fields.get("downloaded_id"),
            "now": datetime.now(timezone.utc),
        },
    )
    await db.commit()


class ResumeRequest(BaseModel):
    text: str


@router.post("/resume/{thread_id}")
@_limiter.limit("20/minute")
async def resume(
    thread_id: str,
    body: ResumeRequest,
    request: Request,
    tenant_slug: str = Depends(verify_tenant_scoped_key),
):
    if not validate_thread_id(thread_id):
        raise HTTPException(status_code=422, detail="Invalid thread_id format")

    # verify_tenant_scoped_key only proves which tenant the key belongs to —
    # thread_id embeds its own tenant slug, so a valid key for tenant A could
    # still target tenant B's thread without this explicit cross-check.
    if parse_thread_part(thread_id, "tenant") != tenant_slug:
        raise HTTPException(status_code=403, detail="Thread does not belong to this tenant")

    from langgraph.types import Command

    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}

    result = await graph.ainvoke(Command(resume=body.text), config=config)

    # Mark interrupt resolved in audit
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE conversation_audit
                       SET expired_at = :now,
                           bot_response = :resp
                     WHERE thread_id = :tid
                       AND expired_at IS NULL
                       AND interrupt_started_at IS NOT NULL
                """),
                {
                    "now": datetime.now(timezone.utc),
                    "resp": body.text,
                    "tid": thread_id,
                },
            )
            await db.commit()
    except Exception as exc:
        # Non-fatal — audit failure must not block the operator response
        import logging
        logging.getLogger(__name__).warning("audit_update_failed thread=%s err=%s", thread_id, exc)

    answer = body.text
    if result and isinstance(result, dict):
        answer = result.get("answer") or answer

    return {"status": "resumed", "thread_id": thread_id, "answer": answer}


@router.get("/pending")
async def list_pending(
    tenant_slug: str = Depends(verify_tenant_scoped_key),
):
    """List threads currently waiting for operator response, scoped to the caller's tenant."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT ca.thread_id, ca.user_id, ca.channel, ca.user_message, ca.interrupt_started_at
                  FROM conversation_audit ca
                  JOIN tenants t ON t.id = ca.tenant_id
                 WHERE ca.expired_at IS NULL
                   AND ca.interrupt_started_at IS NOT NULL
                   AND t.slug = :slug
                 ORDER BY ca.interrupt_started_at
            """),
            {"slug": tenant_slug},
        )
        return [dict(r._mapping) for r in rows.fetchall()]


@router.get("/patient-search")
async def patient_search_endpoint(
    name: str,
    dni_or_dob: str,
    x_operator_identity: str = Header(...),
    tenant_slug: str = Depends(verify_tenant_scoped_key),
):
    if not x_operator_identity.strip():
        raise HTTPException(status_code=422, detail="X-Operator-Identity is required")

    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")

        try:
            result = await patient_search_svc.patient_search(db, tenant, name, dni_or_dob)
        except patient_search_svc.SearchValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except patient_search_svc.PatientNotFoundError:
            result = {"patient_name": name, "results": [], "message": "no se encontró"}
        except patient_search_svc.AmbiguousPatientError as exc:
            result = {"ambiguous": True, "candidates": exc.candidates}
        except google_oauth.RefreshError as exc:
            raise HTTPException(
                status_code=409, detail="Reconectá Google para este tenant"
            ) from exc

        result_ids_shown = json.dumps([r["result_id"] for r in result.get("results", [])])
        try:
            await _insert_patient_search_audit(
                db,
                tenant_id=tenant.id,
                operator_identity=x_operator_identity,
                action="search",
                query_name=name,
                query_dni_or_dob=dni_or_dob,
                result_ids_shown=result_ids_shown,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="No se pudo registrar la auditoría, intentá de nuevo"
            ) from exc

    return result


@router.get("/patient-search/{result_id}/download")
async def download_patient_search_result(
    result_id: str,
    x_operator_identity: str = Header(...),
    tenant_slug: str = Depends(verify_tenant_scoped_key),
):
    if not x_operator_identity.strip():
        raise HTTPException(status_code=422, detail="X-Operator-Identity is required")

    try:
        envelope = patient_search_svc.verify_result_id(result_id)
    except patient_search_svc.SearchValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(select(Tenant).where(Tenant.slug == tenant_slug))).scalar_one_or_none()
        if not tenant or envelope.get("tenant_id") != tenant.id:
            raise HTTPException(status_code=403, detail="result_id does not belong to this tenant")

        if not tenant.google_refresh_token:
            raise HTTPException(status_code=409, detail="Reconectá Google para este tenant")

        try:
            creds = google_oauth.refresh_access_token(tenant.google_refresh_token)
        except google_oauth.RefreshError as exc:
            raise HTTPException(
                status_code=409, detail="Reconectá Google para este tenant"
            ) from exc

        if envelope["source"] == "gmail":
            service = google_build("gmail", "v1", credentials=creds)
            attachment = service.users().messages().attachments().get(
                userId="me", messageId=envelope["message_id"], id=envelope["attachment_id"]
            ).execute()
            data = base64.urlsafe_b64decode(attachment["data"])
        else:
            service = google_build("drive", "v3", credentials=creds)
            data = service.files().get_media(fileId=envelope["file_id"]).execute()

        if len(data) > MAX_MEDIA_BYTES:
            raise HTTPException(status_code=413, detail="Archivo excede el límite de tamaño")

        try:
            await _insert_patient_search_audit(
                db,
                tenant_id=tenant.id,
                operator_identity=x_operator_identity,
                action="download",
                downloaded_id=result_id,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=503, detail="No se pudo registrar la auditoría, intentá de nuevo"
            ) from exc

    return Response(content=data, media_type="application/octet-stream")
