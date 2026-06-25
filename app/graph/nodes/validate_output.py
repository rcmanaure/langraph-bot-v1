import logging

from langchain_core.messages import AIMessage

from app.graph.nodes.generate import _FALLBACK
from app.services.security import validate_output_canary
from app.state import AgentState

logger = logging.getLogger(__name__)


async def validate_output(state: AgentState) -> dict:
    answer = (state.get("answer") or "").strip()

    # Canary check — detection only, never blocks
    validate_output_canary(answer, user_id=state.get("thread_id", ""))

    if len(answer) > 10:
        return {}

    # Empty/malformed — retry generate once
    logger.warning("validate_output_empty retrying generate thread=%s", state.get("thread_id"))
    try:
        from app.graph.nodes.generate import generate

        result = await generate(state)
        retry_answer = (result.get("answer") or "").strip()
        if retry_answer:
            validate_output_canary(retry_answer, user_id=state.get("thread_id", ""))
            return result
    except Exception as exc:
        logger.warning("validate_output_retry_failed err=%s", exc)

    # Never 500
    msg = AIMessage(content=_FALLBACK)
    return {"answer": _FALLBACK, "messages": [msg]}
