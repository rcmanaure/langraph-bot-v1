import logging

import tiktoken
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.services.llm import get_embeddings

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")


async def retrieve_chunks(db: AsyncSession, query: str, namespace: str) -> list[dict]:
    query_vec = await get_embeddings().aembed_query(query)

    ef = settings.hnsw_ef_search
    scan = settings.hnsw_iterative_scan
    if scan not in ("off", "relaxed_order", "strict_order"):
        scan = "relaxed_order"

    await db.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef)}"))
    await db.execute(text(f"SET LOCAL hnsw.iterative_scan = {scan}"))

    result = await db.execute(
        text("""
            SELECT content, source, page,
                   1 - (embedding <=> CAST(:qv AS vector)) AS similarity
            FROM document_chunks
            WHERE namespace = :ns AND embedding IS NOT NULL
            ORDER BY embedding <=> CAST(:qv AS vector)
            LIMIT :k
        """),
        {"qv": str(query_vec), "ns": namespace, "k": settings.top_k_results},
    )
    return [
        {
            "content": r.content,
            "source": r.source,
            "page": r.page,
            "similarity": round(float(r.similarity), 3),
        }
        for r in result.fetchall()
    ]


def cap_chunks_to_tokens(chunks: list[dict], max_tokens: int) -> list[dict]:
    kept, total = [], 0
    for chunk in chunks:
        n = len(_enc.encode(chunk["content"]))
        if total + n > max_tokens:
            break
        kept.append(chunk)
        total += n
    return kept
