import json
import logging

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage, trim_messages

from app.config import settings
from app.services.llm import get_chat_llm
from app.state import AgentState

logger = logging.getLogger(__name__)

_enc = tiktoken.get_encoding("cl100k_base")

_TRIAGE_PROMPT = """\
Classify the user's latest message into ONE category:
- "rag": specific question that needs searching the knowledge base
- "catalog": wants a full list/catalog/prices of all products or services
- "human": explicitly asks to speak with a human, operator, or agent
- "off_topic": completely unrelated to the business

Reply ONLY with JSON: {"decision": "<category>"}
"""


def _token_counter(msgs) -> int:
    return sum(len(_enc.encode(m.content if isinstance(m.content, str) else "")) for m in msgs)


async def triage(state: AgentState) -> dict:
    last = next(
        (m for m in reversed(state["messages"]) if isinstance(m, HumanMessage)),
        None,
    )
    if not last:
        return {"triage_decision": "rag"}

    # trim history so triage stays cheap
    trimmed = trim_messages(
        state["messages"],
        max_tokens=settings.history_max_tokens,
        strategy="last",
        token_counter=_token_counter,
        allow_partial=False,
        include_system=True,
    )

    llm = get_chat_llm()
    resp = await llm.ainvoke([SystemMessage(content=_TRIAGE_PROMPT)] + trimmed)

    try:
        decision = json.loads(resp.content.strip())["decision"]
        if decision not in ("rag", "catalog", "human", "off_topic"):
            decision = "rag"
    except Exception:
        logger.warning("triage_parse_failed resp=%r", resp.content[:100])
        decision = "rag"

    return {"triage_decision": decision}
