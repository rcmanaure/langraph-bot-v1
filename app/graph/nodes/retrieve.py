from langchain_core.messages import HumanMessage

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.rag import cap_chunks_to_tokens, retrieve_chunks
from app.state import AgentState


def _last_human_query(state: AgentState) -> str:
    return next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )


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

    return {"retrieved_chunks": cap_chunks_to_tokens(chunks, settings.retrieval_max_tokens)}
