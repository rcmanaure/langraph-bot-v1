import logging

from langchain_core.messages import AIMessage, SystemMessage, trim_messages
from sqlalchemy import text

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.llm import get_chat_llm
from app.services.rag import cap_chunks_to_tokens, token_counter
from app.state import AgentState

logger = logging.getLogger(__name__)

_FORMAT_HINT = """
Formato (OBLIGATORIO — compatible WhatsApp/Telegram):
- Tono: cálido y cercano, como una persona del negocio respondiendo por chat. Nada de lenguaje robótico.
- Puedes abrir con una frase corta y natural si corresponde (ej. "Claro, eso lo tenemos 👌" o "Sí, existe:").
- BREVE: máximo 4-5 líneas en total. Sin párrafos largos.
- *negrita* con asteriscos simples para códigos y nombres de ítems.
- _cursiva_ con guiones bajos para notas o aclaraciones breves.
- Listas con guión (- item). Sin tablas, sin encabezados Markdown (##).
- Por ítem: - *CÓDIGO* Nombre: $precio"""

_RAG_SYSTEM = """\
Eres un asistente de {expertise}. Eres amable y cercano, como alguien del negocio respondiendo por WhatsApp.
Usa ÚNICAMENTE el contexto proporcionado. NO uses conocimiento propio fuera de ese contexto.

REGLAS (en orden de prioridad):
1. AMBIGÜEDAD: Si lo que pide el usuario puede referirse a varios ítems distintos, haz UNA sola pregunta breve y amable de aclaración. No asumas.
2. COINCIDENCIA EXACTA: Muestra TODOS los ítems del contexto cuyo nombre coincida con lo que el usuario menciona, sin filtrar por categoría o tipo.
3. APROXIMACIÓN: Si el ítem exacto no está en el contexto pero hay algo relacionado, preséntalo de forma natural y pregunta: "¿Eso es lo que necesitas?" NO eleves al contacto todavía — espera la confirmación del usuario.
4. CONFIRMACIÓN NEGATIVA: Si el usuario responde que la aproximación NO es lo que busca, o si definitivamente no hay nada relacionado, di en una línea que no lo ofrecemos y eleva al contacto: {contact_hint}
- NO inventes precios ni servicios.
{format_hint}
Contexto:
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
        token_counter=token_counter,
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
