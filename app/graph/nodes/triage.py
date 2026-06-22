import json
import logging

import tiktoken
from langchain_core.messages import HumanMessage, SystemMessage, trim_messages
from pydantic import ValidationError

from app.config import settings
from app.schemas.triage import TriageDecision
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
    if not any(isinstance(m, HumanMessage) for m in state["messages"]):
        return {"triage_decision": "rag"}

    trimmed = trim_messages(
        state["messages"],
        max_tokens=settings.history_max_tokens,
        strategy="last",
        token_counter=_token_counter,
        allow_partial=False,
        include_system=True,
    )

    llm = get_chat_llm()
    payload = [SystemMessage(content=_TRIAGE_PROMPT)] + trimmed

    # Primary: structured output (function calling)
    try:
        result: TriageDecision = await llm.with_structured_output(TriageDecision).ainvoke(payload)
        return {"triage_decision": result.decision}
    except (ValidationError, Exception) as exc:
        logger.warning("triage_structured_failed=%s falling back to json parse", exc)

    # Fallback: raw LLM + JSON parse
    try:
        resp = await llm.ainvoke(payload)
        decision = json.loads(resp.content.strip())["decision"]
        TriageDecision(decision=decision)  # validate enum
        return {"triage_decision": decision}
    except Exception:
        logger.warning("triage_json_fallback_failed defaulting to rag")
        return {"triage_decision": "rag"}
