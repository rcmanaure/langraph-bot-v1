import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import text

from app.auth import verify_operator_key
from app.db import AsyncSessionLocal
from app.models import IndexJob, IndexJobStatus
from app.services.indexer import run_index_job

router = APIRouter(prefix="/admin", tags=["admin"])


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
async def get_index_job(
    job_id: str,
    _: None = Depends(verify_operator_key),
):
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
async def list_index_jobs(
    tenant_slug: str,
    _: None = Depends(verify_operator_key),
):
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
