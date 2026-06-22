import io
import logging
import uuid

import filetype
import pypdf

from app.config import settings
from app.db import AsyncSessionLocal
from app.models import DocumentChunk, IndexJob, IndexJobStatus
from app.services.llm import get_embeddings

logger = logging.getLogger(__name__)

_EMBED_BATCH = 20  # chunks per embedding call


def _extract_pages(content: bytes, filename: str) -> list[tuple[str, int]]:
    kind = filetype.guess(content)
    if kind and kind.mime == "application/pdf":
        reader = pypdf.PdfReader(io.BytesIO(content))
        return [(page.extract_text() or "", i) for i, page in enumerate(reader.pages)]
    return [(content.decode("utf-8", errors="replace"), 0)]


def _chunk_page(text: str, source: str, page: int) -> list[dict]:
    size, overlap = settings.chunk_size, settings.chunk_overlap
    paragraphs = [p.strip() for p in text.split("\n\n") if len(p.strip()) > 50]
    merged, current = [], ""
    for para in paragraphs:
        if len(para) > size:
            if current:
                merged.append(current)
                current = ""
            for start in range(0, len(para), size - overlap):
                merged.append(para[start : start + size])
        elif not current:
            current = para
        elif len(current) + 2 + len(para) <= size:
            current += "\n\n" + para
        else:
            merged.append(current)
            current = para
    if current:
        merged.append(current)
    return [{"content": c.strip(), "source": source, "page": page} for c in merged if len(c.strip()) > 50]


async def run_index_job(
    job_id: uuid.UUID,
    content: bytes,
    filename: str,
    tenant_id: int,
    namespace: str,
) -> None:
    async with AsyncSessionLocal() as db:
        job = await db.get(IndexJob, job_id)
        if not job:
            logger.error("index_job_not_found job=%s", job_id)
            return
        job.status = IndexJobStatus.RUNNING
        await db.commit()

    try:
        pages = _extract_pages(content, filename)
        all_chunks: list[dict] = []
        for text_content, page_num in pages:
            all_chunks.extend(_chunk_page(text_content, filename, page_num))

        if not all_chunks:
            raise ValueError("No content could be extracted from file")

        async with AsyncSessionLocal() as db:
            job = await db.get(IndexJob, job_id)
            job.chunks_total = len(all_chunks)
            await db.commit()

        embedder = get_embeddings()
        done = 0

        for i in range(0, len(all_chunks), _EMBED_BATCH):
            batch = all_chunks[i : i + _EMBED_BATCH]
            vecs = await embedder.aembed_documents([c["content"] for c in batch])

            async with AsyncSessionLocal() as db:
                db.add_all([
                    DocumentChunk(
                        tenant_id=tenant_id,
                        job_id=job_id,
                        namespace=namespace,
                        source=c["source"],
                        page=c["page"],
                        content=c["content"],
                        embedding=vec,
                    )
                    for c, vec in zip(batch, vecs)
                ])
                done += len(batch)
                job = await db.get(IndexJob, job_id)
                job.chunks_done = done
                await db.commit()

        async with AsyncSessionLocal() as db:
            job = await db.get(IndexJob, job_id)
            job.status = IndexJobStatus.DONE
            await db.commit()

        logger.info("index_job_done job=%s chunks=%d", job_id, done)

    except Exception as exc:
        logger.exception("index_job_failed job=%s", job_id)
        async with AsyncSessionLocal() as db:
            job = await db.get(IndexJob, job_id)
            if job:
                job.status = IndexJobStatus.FAILED
                job.error_message = str(exc)[:500]
                await db.commit()
