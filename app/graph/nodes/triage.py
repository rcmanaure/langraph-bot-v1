import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage, trim_messages

from app.config import settings
from app.schemas.triage import TriageDecision
from app.services.llm import get_chat_llm
from app.services.rag import token_counter
from app.state import AgentState

logger = logging.getLogger(__name__)

_TRIAGE_PROMPT = """\
Classify the user's latest message into ONE category:
- "greeting": ONLY a greeting, thanks, farewell, or social pleasantry with NO question about products/services/prices at all
- "rag": ANY question about a product, service, price, exam, procedure, study, biopsy, analysis, cost, or anything the business might offer — even if vague
- "catalog": explicitly wants a FULL list/catalog/ALL products or services
- "human": explicitly asks to speak with a human, operator, or agent
- "off_topic": ONLY if completely unrelated (politics, weather, sports, jokes, coding questions)

IMPORTANT: Medical terms, body parts, lab tests, procedures, and prices are ALWAYS "rag" —
never "greeting", even if the message opens with "hola" first.
Examples of "greeting": "hola", "buenas", "gracias", "buen día", "hasta luego"
Examples of "rag": "biopsia de pulmon", "cuanto cuesta", "riñon", "análisis de sangre", "histología"
Examples of "off_topic": "quien ganó el partido", "como programo en python", "chiste"

When in doubt between rag/off_topic → "rag". Default is "rag".
Reply ONLY with JSON: {"decision": "<category>"}
"""


async def triage(state: AgentState) -> dict:
    if not any(isinstance(m, HumanMessage) for m in state["messages"]):
        return {"triage_decision": "rag"}

    trimmed = trim_messages(
        state["messages"],
        max_tokens=settings.history_max_tokens,
        strategy="last",
        token_counter=token_counter,
        allow_partial=False,
        include_system=True,
    )

    llm = get_chat_llm()
    payload = [SystemMessage(content=_TRIAGE_PROMPT)] + trimmed

    # Primary: structured output (function calling)
    try:
        result: TriageDecision = await llm.with_structured_output(TriageDecision).ainvoke(payload)
        return {"triage_decision": result.decision}
    except Exception as exc:
        logger.warning("triage_structured_failed=%s falling back to json parse", exc)

    # Fallback: raw LLM + JSON parse (strip markdown fences if present)
    try:
        resp = await llm.ainvoke(payload)
        content = resp.content.strip()
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
        decision = json.loads(content)["decision"]
        td = TriageDecision(decision=decision)  # validate enum
        return {"triage_decision": td.decision}
    except Exception:
        logger.warning("triage_json_fallback_failed defaulting to rag")
        return {"triage_decision": "rag"}
