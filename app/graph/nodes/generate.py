import logging

import tiktoken
from langchain_core.messages import AIMessage, SystemMessage, trim_messages
from sqlalchemy import text

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.llm import get_chat_llm
from app.services.rag import cap_chunks_to_tokens
from app.state import AgentState

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")

_RAG_SYSTEM = """\
You are a helpful assistant. Answer the user's question using ONLY the provided context.
If the context doesn't contain enough information, say so honestly. Do not invent information.

Context:
{context}
"""

_CATALOG_SYSTEM = """\
You are a helpful assistant. List ALL items from the catalog below, organized by section.
Do not omit any item. Use the exact names and prices from the catalog.

Catalog:
{context}
"""


def _token_counter(msgs) -> int:
    return sum(len(_enc.encode(m.content if isinstance(m.content, str) else "")) for m in msgs)


async def generate(state: AgentState) -> dict:
    chunks = list(state.get("retrieved_chunks") or [])
    is_catalog = state.get("triage_decision") == "catalog"

    if is_catalog and not chunks:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                text(
                    "SELECT content FROM document_chunks "
                    "WHERE namespace = :ns AND embedding IS NOT NULL "
                    "ORDER BY id LIMIT 100"
                ),
                {"ns": state["tenant_id"]},
            )
            chunks = [{"content": r.content} for r in result.fetchall()]
            chunks = cap_chunks_to_tokens(chunks, settings.retrieval_max_tokens)

    context = "\n\n---\n\n".join(c["content"] for c in chunks) if chunks else "No context available."
    system = (_CATALOG_SYSTEM if is_catalog else _RAG_SYSTEM).format(context=context)

    trimmed = trim_messages(
        state["messages"],
        max_tokens=settings.history_max_tokens,
        strategy="last",
        token_counter=_token_counter,
        allow_partial=False,
        include_system=True,
    )

    llm = get_chat_llm()
    try:
        response = await llm.ainvoke([SystemMessage(content=system)] + trimmed)
    except Exception as exc:
        if not settings.openai_fallback_model:
            raise
        logger.warning("generate_primary_failed=%s retrying with fallback", exc)
        response = await get_chat_llm(fallback=True).ainvoke(
            [SystemMessage(content=system)] + trimmed
        )

    return {"answer": response.content, "messages": [response]}
