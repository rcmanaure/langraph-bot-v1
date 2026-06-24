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

_FORMAT_HINT = """
Formato de respuesta (Telegram):
- Usa texto plano con emojis ocasionales si ayuda a la claridad.
- Listas con guión (- item), nunca tablas markdown.
- Negritas con *texto* solo para énfasis clave, sin encabezados con #.
- Si hay estudios relacionados en el contexto, inclúyelos al final como lista con sus precios."""

_RAG_SYSTEM = """\
Eres un asistente de {expertise}.
Responde la pregunta del usuario usando ÚNICAMENTE el contexto proporcionado más abajo.
REGLAS ESTRICTAS:
- Si el contexto no menciona el procedimiento o precio específico preguntado, di "No tengo información sobre ese procedimiento específico en este momento."
- NO uses respuestas anteriores de la conversación como referencia de precios.
- NO inventes precios ni procedimientos.
- Cada pregunta debe responderse basándose SOLO en el contexto actual, no en patrones de respuestas previas.{contact_hint}
{format_hint}
Contexto actual:
{context}
"""

_CATALOG_SYSTEM = """\
Eres un asistente de {expertise}.
Lista TODOS los ítems del catálogo a continuación, organizados por sección.
No omitas ningún ítem. Usa los nombres y precios exactos del catálogo.{contact_hint}
{format_hint}
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
    decision = state.get("triage_decision", "rag")
    is_catalog = decision == "catalog"
    tenant_ctx = await _load_tenant(state["tenant_id"])

    logger.info("generate_called decision=%s chunks=%d", decision, len(chunks))

    if decision == "off_topic":
        content = _OFF_TOPIC_MSG.format(**tenant_ctx)
        msg = AIMessage(content=content)
        return {"answer": content, "messages": [msg]}

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
    system = template.format(context=context, format_hint=_FORMAT_HINT, **tenant_ctx)

    trimmed = trim_messages(
        state["messages"],
        max_tokens=settings.history_max_tokens,
        strategy="last",
        token_counter=_token_counter,
        allow_partial=False,
        include_system=True,
    )

    llm = get_chat_llm()
    logger.info("generate_llm_model=%s", llm.model_name)
    try:
        response = await llm.ainvoke([SystemMessage(content=system)] + trimmed)
        logger.info("generate_response=%s", response.content[:80])
    except Exception as exc:
        if not settings.openai_fallback_model:
            raise
        logger.warning("generate_primary_failed=%s retrying with fallback", exc)
        response = await get_chat_llm(fallback=True).ainvoke(
            [SystemMessage(content=system)] + trimmed
        )

    return {"answer": response.content, "messages": [response]}
