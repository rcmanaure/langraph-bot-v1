import asyncio
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text

from app.auth import verify_operator_key
from app.db import AsyncSessionLocal
from app.models import IndexJob, IndexJobStatus
from app.policies import TenantPolicy
from app.services.indexer import run_index_job

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


# ── Index jobs ────────────────────────────────────────────────────────────────

@router.post("/index")
async def create_index_job(
    tenant_slug: str = Form(...),
    file: UploadFile = File(...),
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
        # Count completed index jobs (= uploaded documents)
        doc_count = (await db.scalar(
            text("SELECT COUNT(*) FROM index_jobs WHERE tenant_id = :tid AND status = 'COMPLETED'"),
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

    asyncio.create_task(
        run_index_job(
            job_id=job_id,
            content=content,
            filename=file.filename or "upload",
            tenant_id=tenant_id,
            namespace=tenant_slug,
        )
    )
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

        # Count current usage (completed documents and total chunks)
        doc_count = (await db.scalar(
            text("SELECT COUNT(*) FROM index_jobs WHERE tenant_id = :tid AND status = 'COMPLETED'"),
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
