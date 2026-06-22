import logging

import tiktoken
from langchain_core.messages import SystemMessage, trim_messages
from sqlalchemy import text

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.llm import get_chat_llm
from app.services.rag import cap_chunks_to_tokens
from app.state import AgentState

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")

_RAG_SYSTEM = """\
Eres un asistente de {expertise}.
Responde la pregunta del usuario usando ÚNICAMENTE el contexto proporcionado.
Si el contexto no contiene suficiente información, dilo honestamente.
No inventes información.{contact_hint}

Contexto:
{context}
"""

_CATALOG_SYSTEM = """\
Eres un asistente de {expertise}.
Lista TODOS los ítems del catálogo a continuación, organizados por sección.
No omitas ningún ítem. Usa los nombres y precios exactos del catálogo.{contact_hint}

Catálogo:
{context}
"""

_OFF_TOPIC_MSG = "Lo siento, no puedo ayudarte con eso. Soy un asistente especializado en {expertise}."

_FALLBACK = "Lo siento, no pude procesar tu consulta en este momento. Por favor intenta de nuevo."


def _token_counter(msgs) -> int:
    return sum(len(_enc.encode(m.content if isinstance(m.content, str) else "")) for m in msgs)


async def _load_tenant(slug: str) -> dict:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT expertise_area, contact_url FROM tenants WHERE slug = :s"),
            {"s": slug},
        )).first()
    if not row:
        return {"expertise": "este negocio", "contact_hint": ""}
    expertise = row.expertise_area or "este negocio"
    contact_hint = (f"\nSi necesitas más ayuda, contacta: {row.contact_url}" if row.contact_url else "")
    return {"expertise": expertise, "contact_hint": contact_hint}


async def generate(state: AgentState) -> dict:
    chunks = list(state.get("retrieved_chunks") or [])
    is_catalog = state.get("triage_decision") == "catalog"
    tenant_ctx = await _load_tenant(state["tenant_id"])

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

    context = "\n\n---\n\n".join(c["content"] for c in chunks) if chunks else "Sin contexto disponible."
    template = _CATALOG_SYSTEM if is_catalog else _RAG_SYSTEM
    system = template.format(context=context, **tenant_ctx)

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
