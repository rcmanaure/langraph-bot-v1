import asyncio
import hashlib
import secrets
import time
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.auth import verify_operator_key
from app.channels.telegram import delete_webhook, get_webhook_info, set_webhook
from app.config import settings
from app.crypto import EncryptionNotConfiguredError, require_fernet
from app.db import AsyncSessionLocal
from app.models import IndexJob, IndexJobStatus, IntegrationCredential, StaffSecret, Tenant
from app.policies import TenantPolicy
from app.services.google_api_utils import GOOGLE_SCOPES, encrypt_credentials
from app.services.indexer import run_index_job

_bg_tasks: set[asyncio.Task] = set()

# OAuth CSRF state → tenant slug, short-lived (admin-initiated flow, low
# volume — in-memory is fine, matches other in-process caches in this app).
# Single-worker process (entrypoint.sh --workers 1) makes this safe.
_OAUTH_STATE: dict[str, tuple[str, float]] = {}
_OAUTH_STATE_TTL = 600  # 10 min — plenty for an admin to complete the consent screen

router = APIRouter(prefix="/admin", tags=["admin"])
public_router = APIRouter(tags=["billing"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
async def admin_ui(request: Request):
    return templates.TemplateResponse(request=request, name="admin.html")


# ── Tenants ──────────────────────────────────────────────────────────────────

@router.get("/tenants")
async def list_tenants(_: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT id, slug, expertise_area, contact_url, plan, active, created_at
                  FROM tenants ORDER BY created_at DESC
            """)
        )
        return [dict(r._mapping) for r in rows.fetchall()]


class TenantCreate(BaseModel):
    slug: str
    bot_token: str
    webhook_secret: str
    expertise_area: str = ""
    contact_url: str = ""
    plan: str = "free"


@router.post("/tenants", status_code=201)
async def create_tenant(body: TenantCreate, _: None = Depends(verify_operator_key)):
    raw_api_key = secrets.token_hex(32)
    api_key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()

    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            text("SELECT id FROM tenants WHERE slug = :slug OR bot_token = :token"),
            {"slug": body.slug, "token": body.bot_token},
        )).first()
        if existing:
            raise HTTPException(status_code=409, detail="Slug o bot_token ya existe")

        await db.execute(
            text("""
                INSERT INTO tenants
                    (slug, api_key_hash, webhook_secret, bot_token, plan,
                     expertise_area, contact_url, active)
                VALUES
                    (:slug, :api_key_hash, :webhook_secret, :bot_token, :plan,
                     :expertise_area, :contact_url, true)
            """),
            {
                "slug": body.slug,
                "api_key_hash": api_key_hash,
                "webhook_secret": body.webhook_secret,
                "bot_token": body.bot_token,
                "plan": body.plan,
                "expertise_area": body.expertise_area,
                "contact_url": body.contact_url,
            },
        )
        await db.commit()

    return {"slug": body.slug, "api_key": raw_api_key}


class TenantPatch(BaseModel):
    plan: Literal["free", "basic", "pro"] | None = None
    expertise_area: str | None = None
    contact_url: str | None = None
    active: bool | None = None
    # Credential fields — only updated when non-empty string is provided
    bot_token: str | None = None
    webhook_secret: str | None = None
    wa_phone_number_id: str | None = None
    wa_access_token: str | None = None
    wa_app_secret: str | None = None
    wa_verify_token: str | None = None


@router.patch("/tenants/{slug}")
async def patch_tenant(slug: str, body: TenantPatch, _: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            raise HTTPException(status_code=404, detail="Tenant not found")

        fields = body.model_fields_set
        # Capture pre-patch state needed for webhook decisions
        was_active = t.active
        old_token = t.bot_token

        if "plan" in fields:
            t.plan = body.plan
        if "expertise_area" in fields:
            t.expertise_area = body.expertise_area
        if "contact_url" in fields:
            t.contact_url = body.contact_url
        if "active" in fields:
            t.active = body.active
        if "webhook_secret" in fields and body.webhook_secret:
            t.webhook_secret = body.webhook_secret
        if "wa_phone_number_id" in fields:
            t.wa_phone_number_id = body.wa_phone_number_id or None
        if "wa_access_token" in fields and body.wa_access_token:
            t.wa_access_token = body.wa_access_token
        if "wa_app_secret" in fields and body.wa_app_secret:
            t.wa_app_secret = body.wa_app_secret
        if "wa_verify_token" in fields:
            t.wa_verify_token = body.wa_verify_token or None

        token_changed = "bot_token" in fields and bool(body.bot_token)
        if token_changed:
            conflict = (await db.execute(
                select(Tenant.id).where(Tenant.bot_token == body.bot_token, Tenant.id != t.id)
            )).scalar_one_or_none()
            if conflict:
                raise HTTPException(status_code=409, detail="bot_token already in use by another tenant")
            t.bot_token = body.bot_token

        secret_changed = "webhook_secret" in fields and bool(body.webhook_secret)

        try:
            await db.commit()
            await db.refresh(t)
        except IntegrityError:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Conflict updating tenant — check bot_token uniqueness")

    # ── Webhook registration (outside the DB session) ──────────────────────────
    # Clean up old token's registration whenever it's being replaced.
    needs_deregister = (not t.active and was_active) or (t.active and token_changed)
    needs_register = t.active and (token_changed or secret_changed or not was_active)

    webhook_registered = None
    if needs_deregister:
        await delete_webhook(old_token)
    if needs_register:
        webhook_url = f"https://{settings.app_domain}/webhook/telegram/{t.slug}"
        webhook_registered = await set_webhook(t.bot_token, webhook_url, t.webhook_secret)

    # Never return credential fields in the response
    return {
        "slug": t.slug,
        "plan": t.plan,
        "expertise_area": t.expertise_area,
        "contact_url": t.contact_url,
        "active": t.active,
        "webhook_registered": webhook_registered,
    }


class TenantDelete(BaseModel):
    confirm_slug: str


@router.delete("/tenants/{slug}")
async def delete_tenant(slug: str, body: TenantDelete, _: None = Depends(verify_operator_key)):
    if body.confirm_slug != slug:
        raise HTTPException(status_code=400, detail="confirm_slug does not match tenant slug")

    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            raise HTTPException(status_code=404, detail="Tenant not found")

        running = (await db.scalar(
            text("SELECT COUNT(*) FROM index_jobs WHERE tenant_id = :tid AND status IN ('PENDING', 'RUNNING')"),
            {"tid": t.id},
        )) or 0
        if running:
            raise HTTPException(
                status_code=409,
                detail=f"{running} index job(s) still in progress — wait for them to finish before deleting",
            )

        bot_token = t.bot_token
        thread_prefix = f"tenant:{slug}:%"

        # LangGraph checkpoint tables have no FK to tenants — clean them manually.
        # These tables may not exist in fresh environments; errors are suppressed.
        for lg_table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            try:
                await db.execute(
                    text(f"DELETE FROM {lg_table} WHERE thread_id LIKE :prefix"),  # noqa: S608
                    {"prefix": thread_prefix},
                )
            except Exception:
                pass

        # Long-term profile memory (Store) namespace is (tenant_slug, "channel:user_id"),
        # serialized as "{slug}.channel:user_id" in the prefix column — same cleanup pattern.
        try:
            await db.execute(
                text("DELETE FROM store WHERE prefix LIKE :prefix"),
                {"prefix": f"{slug}.%"},
            )
        except Exception:
            pass

        try:
            await db.delete(t)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail="Cannot delete: tenant has dependent rows. Run: alembic upgrade 0002",
            )

    # Best-effort webhook cleanup outside the DB session
    await delete_webhook(bot_token)

    return {"slug": slug, "deleted": True}


# ── Webhook status ───────────────────────────────────────────────────────────

@router.get("/tenants/{slug}/webhook-status")
async def webhook_status(slug: str, _: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT bot_token FROM tenants WHERE slug = :slug"),
            {"slug": slug},
        )).first()
    if not row:
        raise HTTPException(status_code=404, detail="Tenant not found")

    info = await get_webhook_info(row.bot_token)
    if not info["ok"]:
        return {"status": "error", "detail": info.get("error")}

    registered_url = info["result"].get("url", "")
    expected_url = f"https://{settings.app_domain}/webhook/telegram/{slug}"

    if not registered_url:
        status = "unknown"
    elif registered_url == expected_url:
        status = "registered"
    else:
        # Webhook exists but points to a different URL — config drift or domain change
        status = "mismatch"

    return {"status": status, "url": registered_url}


# ── API key rotation ─────────────────────────────────────────────────────────

@router.post("/tenants/{slug}/regen-key")
async def regen_api_key(slug: str, _: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(Tenant).where(Tenant.slug == slug))).scalar_one_or_none()
        if not t:
            raise HTTPException(status_code=404, detail="Tenant not found")

        raw_key = secrets.token_hex(32)
        t.api_key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        await db.commit()

    # Return the raw key exactly once — it is never stored and cannot be recovered.
    return {"api_key": raw_key}


# ── Index jobs ────────────────────────────────────────────────────────────────

@router.get("/tenants/{slug}/chunks/files")
async def list_chunk_files(slug: str, _: None = Depends(verify_operator_key)):
    """List distinct source files indexed for a tenant, with chunk counts."""
    async with AsyncSessionLocal() as db:
        tenant_id = await db.scalar(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": slug},
        )
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

        rows = await db.execute(
            text("""
                SELECT SPLIT_PART(source, ':', 1) AS filename,
                       COUNT(*) AS chunk_count
                  FROM document_chunks
                 WHERE tenant_id = :tid
                 GROUP BY SPLIT_PART(source, ':', 1)
                 ORDER BY filename
            """),
            {"tid": tenant_id},
        )
        return [dict(r._mapping) for r in rows.fetchall()]


@router.delete("/tenants/{slug}/chunks")
async def delete_chunks(
    slug: str,
    source: str | None = None,
    _: None = Depends(verify_operator_key),
):
    """Delete document chunks for a tenant.

    - Without ?source: deletes ALL chunks (full namespace wipe).
    - With ?source=filename.md: deletes only chunks from that file.

    Use before re-uploading a document to avoid duplicate chunks.
    """
    async with AsyncSessionLocal() as db:
        tenant_id = (await db.scalar(
            text("SELECT id FROM tenants WHERE slug = :slug"),
            {"slug": slug},
        ))
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

        if source:
            # For JSONL files delete by source prefix (filename:id pattern)
            result = await db.execute(
                text("""
                    DELETE FROM document_chunks
                     WHERE tenant_id = :tid
                       AND (source = :src OR source LIKE :prefix)
                """),
                {"tid": tenant_id, "src": source, "prefix": f"{source}:%"},
            )
        else:
            result = await db.execute(
                text("DELETE FROM document_chunks WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
        deleted = result.rowcount
        await db.commit()

    return {"deleted": deleted, "source": source or "*"}


@router.post("/index")
async def create_index_job(
    tenant_slug: str = Form(...),
    file: UploadFile = File(...),
    replace_all: bool = Form(False),
    _: None = Depends(verify_operator_key),
):
    content = await file.read()

    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT id, plan FROM tenants WHERE slug = :slug AND active = true"),
            {"slug": tenant_slug},
        )).first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        tenant_id, plan = row

        # Check document limit based on plan
        policy = TenantPolicy(tenant_slug=tenant_slug, plan=plan)
        # Count distinct documents currently active in document_chunks (source
        # is "{filename}:{item_id}") — NOT historical index_jobs. replace_all
        # deletes stale document_chunks rows on re-upload, so counting jobs
        # instead would count every re-upload attempt against the plan limit
        # forever, defeating replace_all's purpose of freeing up quota.
        doc_count = (await db.scalar(
            text(
                "SELECT COUNT(DISTINCT split_part(source, ':', 1)) "
                "FROM document_chunks WHERE tenant_id = :tid"
            ),
            {"tid": tenant_id},
        )) or 0

        if not policy.can_upload_doc(doc_count):
            max_docs = policy._limit("docs")
            raise HTTPException(
                status_code=429,
                detail=f"Document limit ({max_docs}) reached for {plan.upper()} plan. Upgrade to add more."
            )

        job = IndexJob(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            filename=file.filename or "upload",
            status=IndexJobStatus.PENDING,
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    task = asyncio.create_task(
        run_index_job(
            job_id=job_id,
            content=content,
            filename=file.filename or "upload",
            tenant_id=tenant_id,
            namespace=tenant_slug,
            replace_all=replace_all,
        )
    )
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return {"job_id": str(job_id), "status": "PENDING"}


@router.get("/index/{job_id}")
async def get_index_job(job_id: str, _: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT id, filename, status, chunks_total, chunks_done,
                       error_message, created_at, updated_at
                  FROM index_jobs WHERE id = :id
            """),
            {"id": job_id},
        )
        row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    return dict(row._mapping)


@router.get("/index")
async def list_index_jobs(tenant_slug: str, _: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            text("""
                SELECT j.id, j.filename, j.status, j.chunks_total, j.chunks_done,
                       j.error_message, j.created_at, j.updated_at
                  FROM index_jobs j
                  JOIN tenants t ON t.id = j.tenant_id
                 WHERE t.slug = :slug
                 ORDER BY j.created_at DESC
                 LIMIT 50
            """),
            {"slug": tenant_slug},
        )
        return [dict(r._mapping) for r in result.fetchall()]


# ── Plan & Quotas ────────────────────────────────────────────────────────────

@router.get("/billing/{tenant_slug}")
async def get_tenant_billing(tenant_slug: str, _: None = Depends(verify_operator_key)):
    """Get plan, pricing, and current usage for a tenant."""
    async with AsyncSessionLocal() as db:
        tenant = (await db.execute(
            text("SELECT id, plan FROM tenants WHERE slug = :slug AND active = true"),
            {"slug": tenant_slug},
        )).first()
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        tenant_id, plan = tenant

        # Count current usage (active documents and total chunks) — see the
        # matching comment in create_index_job for why this counts distinct
        # document_chunks sources rather than historical index_jobs.
        doc_count = (await db.scalar(
            text(
                "SELECT COUNT(DISTINCT split_part(source, ':', 1)) "
                "FROM document_chunks WHERE tenant_id = :tid"
            ),
            {"tid": tenant_id},
        )) or 0
        chunk_count = (await db.scalar(
            text("SELECT COUNT(*) FROM document_chunks WHERE tenant_id = :tid"),
            {"tid": tenant_id},
        )) or 0

    policy = TenantPolicy(tenant_slug=tenant_slug, plan=plan)
    limits = policy.get_limits()
    pricing = policy.get_pricing()

    return {
        "plan": plan,
        "price_usd": pricing["price_usd"],
        "limits": {
            "documents": {"current": doc_count, "max": limits[0]},
            "chunks": {"current": chunk_count, "max": limits[1]},
            "queries_monthly": {"current": 0, "max": limits[2]},  # TODO: track queries
        },
        "usage_percent": {
            "documents": round((doc_count / limits[0] * 100) if limits[0] > 0 else 0, 1),
            "chunks": round((chunk_count / limits[1] * 100) if limits[1] > 0 else 0, 1),
        }
    }


# ── Lab staff search: staff secrets (T1) ────────────────────────────────────

class StaffSecretCreate(BaseModel):
    label: str


@router.post("/tenants/{slug}/staff-secrets", status_code=201)
async def issue_staff_secret(
    slug: str, body: StaffSecretCreate, _: None = Depends(verify_operator_key)
):
    """Issue a new named, per-employee unlock secret. Returned exactly once —
    only the hash is stored, matching the raw_api_key pattern (T1)."""
    async with AsyncSessionLocal() as db:
        tenant_id = await db.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

        raw_secret = secrets.token_hex(24)
        secret = StaffSecret(
            tenant_id=tenant_id,
            label=body.label,
            secret_hash=hashlib.sha256(raw_secret.encode()).hexdigest(),
        )
        db.add(secret)
        await db.commit()
        await db.refresh(secret)

    return {"id": secret.id, "label": secret.label, "secret": raw_secret}


@router.get("/tenants/{slug}/staff-secrets")
async def list_staff_secrets(slug: str, _: None = Depends(verify_operator_key)):
    """Never returns the secret itself — only enough to manage it."""
    async with AsyncSessionLocal() as db:
        tenant_id = await db.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

        rows = (await db.execute(
            select(StaffSecret).where(StaffSecret.tenant_id == tenant_id)
            .order_by(StaffSecret.created_at.desc())
        )).scalars().all()

    return [
        {
            "id": s.id,
            "label": s.label,
            "bound": s.bound_user_id is not None,
            "revoked": s.revoked_at is not None,
            "created_at": s.created_at,
        }
        for s in rows
    ]


@router.post("/tenants/{slug}/staff-secrets/{secret_id}/revoke")
async def revoke_staff_secret(
    slug: str, secret_id: int, _: None = Depends(verify_operator_key)
):
    async with AsyncSessionLocal() as db:
        secret = (await db.execute(
            select(StaffSecret)
            .join(Tenant, Tenant.id == StaffSecret.tenant_id)
            .where(Tenant.slug == slug, StaffSecret.id == secret_id)
        )).scalar_one_or_none()
        if not secret:
            raise HTTPException(status_code=404, detail="Staff secret not found")

        await db.execute(
            text("UPDATE staff_secrets SET revoked_at = now() WHERE id = :id"),
            {"id": secret_id},
        )
        await db.commit()

    return {"id": secret_id, "revoked": True}


# ── Lab staff search: Google OAuth connection (T3) ──────────────────────────

def _require_oauth_config() -> None:
    if not (settings.google_oauth_client_id and settings.google_oauth_client_secret
            and settings.google_oauth_redirect_uri):
        raise HTTPException(
            status_code=500,
            detail="Google OAuth is not configured (GOOGLE_OAUTH_CLIENT_ID/SECRET/REDIRECT_URI)",
        )


def _build_oauth_flow():
    from google_auth_oauthlib.flow import Flow

    return Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_oauth_client_id,
                "client_secret": settings.google_oauth_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_oauth_redirect_uri],
            }
        },
        scopes=GOOGLE_SCOPES,
        redirect_uri=settings.google_oauth_redirect_uri,
    )


def _purge_expired_oauth_state() -> None:
    now = time.monotonic()
    expired = [k for k, (_, exp) in _OAUTH_STATE.items() if exp < now]
    for k in expired:
        _OAUTH_STATE.pop(k, None)


@router.get("/tenants/{slug}/google/connect")
async def google_connect_start(slug: str, _: None = Depends(verify_operator_key)):
    """Admin-initiated: returns the Google consent URL for this tenant's
    shared Drive/Gmail connection. Admin opens it in a browser and completes
    the consent screen — this is a one-time setup step, not a chat flow."""
    _require_oauth_config()
    async with AsyncSessionLocal() as db:
        exists = await db.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if not exists:
            raise HTTPException(status_code=404, detail="Tenant not found")

    _purge_expired_oauth_state()
    state = secrets.token_urlsafe(24)
    _OAUTH_STATE[state] = (slug, time.monotonic() + _OAUTH_STATE_TTL)

    flow = _build_oauth_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline", include_granted_scopes="true",
        prompt="consent",  # force refresh_token issuance even on re-consent
        state=state,
    )
    return {"authorization_url": auth_url}


@router.get("/google/oauth/callback", include_in_schema=False)
async def google_oauth_callback(request: Request, state: str, code: str | None = None,
                                  error: str | None = None):
    """Google redirects here after the admin completes (or cancels) consent.
    Not behind verify_operator_key — Google itself calls this URL — the CSRF
    `state` token is the auth boundary instead."""
    _purge_expired_oauth_state()
    entry = _OAUTH_STATE.pop(state, None)
    if not entry:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")
    slug, _ = entry

    if error:
        return {"connected": False, "error": error}

    flow = _build_oauth_flow()
    try:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: flow.fetch_token(code=code)
        )
    except Exception as exc:
        return {"connected": False, "error": str(exc)}

    creds = flow.credentials

    try:
        require_fernet()
    except EncryptionNotConfiguredError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    async with AsyncSessionLocal() as db:
        tenant_id = await db.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

        existing = (await db.execute(
            select(IntegrationCredential).where(
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.integration_type == "google_drive_gmail",
            )
        )).scalar_one_or_none()

        encrypted = encrypt_credentials(creds)
        if existing:
            existing.encrypted_credentials = encrypted
        else:
            db.add(IntegrationCredential(
                tenant_id=tenant_id,
                integration_type="google_drive_gmail",
                encrypted_credentials=encrypted,
            ))
        await db.commit()

    return {"connected": True, "slug": slug}


@router.post("/tenants/{slug}/google/revoke")
async def google_revoke(slug: str, _: None = Depends(verify_operator_key)):
    async with AsyncSessionLocal() as db:
        tenant_id = await db.scalar(select(Tenant.id).where(Tenant.slug == slug))
        if not tenant_id:
            raise HTTPException(status_code=404, detail="Tenant not found")

        cred = (await db.execute(
            select(IntegrationCredential).where(
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.integration_type == "google_drive_gmail",
            )
        )).scalar_one_or_none()
        if not cred:
            raise HTTPException(status_code=404, detail="No Google connection for this tenant")

        await db.delete(cred)
        await db.commit()

    return {"slug": slug, "disconnected": True}


@public_router.get("/pricing")
async def get_pricing():
    """Get pricing and plan details (no auth required)."""
    plans = {}
    for plan_name in ["free", "basic", "pro"]:
        policy = TenantPolicy(tenant_slug="pricing", plan=plan_name)
        pricing_info = policy.get_pricing()
        plans[plan_name] = {
            "price_usd": pricing_info["price_usd"],
            "billing": "free" if plan_name == "free" else "monthly",
            "limits": pricing_info["limits"],
        }
    return {"plans": plans}
