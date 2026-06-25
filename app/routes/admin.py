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
from app.services.indexer import run_index_job

router = APIRouter(prefix="/admin", tags=["admin"])
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
            text("SELECT id FROM tenants WHERE slug = :slug AND active = true"),
            {"slug": tenant_slug},
        )).first()
        if not row:
            raise HTTPException(status_code=404, detail="Tenant not found")
        tenant_id = row.id

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
