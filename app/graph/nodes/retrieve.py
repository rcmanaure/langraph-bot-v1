import re

from langchain_core.messages import HumanMessage

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.rag import cap_chunks_to_tokens, retrieve_chunks
from app.services.rerank import rerank_chunks
from app.state import AgentState

# A bare confirmation ("si", "sí", "correcto"...) has no retrievable content of
# its own — it's answering the bot's PREVIOUS question (e.g. an approximation
# offer: "¿Eso es lo que necesitas?"). Embedding it verbatim searches for
# nothing meaningful and returns unrelated chunks, so generate() then can't
# find the item it just offered and contradicts itself. Fall back to the
# previous human query so retrieval stays anchored to what's actually being
# confirmed.
_CONFIRMATION_RE = re.compile(
    r"^\s*(si|sí|s|claro|dale|ok|okay|correcto|exacto|eso mismo|as[ií] es|afirmativo)\s*[.!¡]*\s*$",
    re.IGNORECASE,
)


def _last_human_query(state: AgentState) -> str:
    humans = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    if not humans:
        return ""
    last = humans[-1]
    if len(humans) >= 2 and isinstance(last, str) and _CONFIRMATION_RE.match(last):
        return humans[-2]
    return last


def cache_key(state: AgentState) -> str:
    """Cache key for the retrieve node: same tenant + same question -> same chunks.

    Deliberately narrower than the default (whole-state) key, since state also
    carries thread_id and full message history, which are unique per user and
    would defeat caching for the common case of two different users asking the
    same question.
    """
    return f"{state.get('tenant_id', '')}::{_last_human_query(state)}"


async def retrieve(state: AgentState) -> dict:
    query = _last_human_query(state)
    if not query:
        return {"retrieved_chunks": []}

    async with AsyncSessionLocal() as db:
        chunks = await retrieve_chunks(db, query, state["tenant_id"])

    chunks = await rerank_chunks(query, chunks, settings.top_k_results)
    return {"retrieved_chunks": cap_chunks_to_tokens(chunks, settings.retrieval_max_tokens)}
