import logging

from langchain_core.messages import SystemMessage

from app.config import settings
from app.schemas.rerank import RerankResult
from app.services.llm import get_chat_llm

logger = logging.getLogger(__name__)

_RERANK_PROMPT = """\
Sos un sistema de relevancia para un buscador de catálogo. Te doy una consulta de \
usuario y una lista numerada de candidatos recuperados por búsqueda híbrida \
(semántica + palabras clave). Tu ÚNICA tarea es reordenar los candidatos por qué \
tan bien responden la consulta — no respondas la consulta, no agregues candidatos \
nuevos, no inventes nada que no esté en la lista.

Consulta: {query}

Candidatos:
{listing}

Devolvé los índices de los {top_k} candidatos más relevantes, en orden de \
relevancia descendente (el más relevante primero). Si menos de {top_k} candidatos \
son realmente relevantes, devolvé solo esos.
"""


async def rerank_chunks(query: str, chunks: list[dict], top_k: int) -> list[dict]:
    """Re-rank hybrid-search candidates with the chat LLM before generation.

    Hybrid search (dense + keyword, RRF-fused) optimizes for recall — its
    fused score is a rank position blend, not a relevance judgment an LLM
    would agree with. This trades one extra LLM round-trip for a shot at
    better precision on the final top_k that actually reaches the prompt.
    Any failure (rate limit, malformed output, disabled via config) falls
    back to the hybrid order as-is — reranking is a quality improvement,
    never a hard dependency for retrieval to work.
    """
    if not settings.rerank_enabled or len(chunks) <= top_k:
        return chunks[:top_k]

    listing = "\n".join(f"[{i}] {c['content'][:300]}" for i, c in enumerate(chunks))
    prompt = _RERANK_PROMPT.format(query=query, listing=listing, top_k=top_k)
    llm = get_chat_llm()

    try:
        result: RerankResult = await llm.with_structured_output(RerankResult).ainvoke(
            [SystemMessage(content=prompt)]
        )
        indices = result.ranked_indices
    except Exception as exc:
        logger.warning("rerank_failed=%s falling back to hybrid order", exc)
        return chunks[:top_k]

    valid = [i for i in indices if 0 <= i < len(chunks)]
    if not valid:
        logger.warning("rerank_no_valid_indices falling back to hybrid order")
        return chunks[:top_k]

    reranked = [chunks[i] for i in valid[:top_k]]
    if len(reranked) < top_k:
        # Model returned fewer relevant indices than top_k — backfill with
        # the remaining hybrid-order candidates rather than under-filling.
        seen = set(valid[:top_k])
        for i, c in enumerate(chunks):
            if len(reranked) >= top_k:
                break
            if i not in seen:
                reranked.append(c)
    return reranked
