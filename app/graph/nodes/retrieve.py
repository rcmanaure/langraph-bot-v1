from langchain_core.messages import HumanMessage

from app.config import settings
from app.db import AsyncSessionLocal
from app.services.rag import cap_chunks_to_tokens, retrieve_chunks
from app.state import AgentState


async def retrieve(state: AgentState) -> dict:
    query = next(
        (m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        "",
    )
    if not query:
        return {"retrieved_chunks": []}

    async with AsyncSessionLocal() as db:
        chunks = await retrieve_chunks(db, query, state["tenant_id"])

    return {"retrieved_chunks": cap_chunks_to_tokens(chunks, settings.retrieval_max_tokens)}
