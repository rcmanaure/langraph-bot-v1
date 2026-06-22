import logging

from langchain_core.messages import AIMessage

from app.state import AgentState

logger = logging.getLogger(__name__)

_FALLBACK = "Lo siento, no pude procesar tu consulta en este momento. Por favor intenta de nuevo."


async def validate_output(state: AgentState) -> dict:
    answer = (state.get("answer") or "").strip()
    if len(answer) > 10:
        return {}

    # Empty/malformed answer — retry generate once
    logger.warning("validate_output_empty retrying generate thread=%s", state.get("thread_id"))
    try:
        from app.graph.nodes.generate import generate

        result = await generate(state)
        if (result.get("answer") or "").strip():
            return result
    except Exception as exc:
        logger.warning("validate_output_retry_failed err=%s", exc)

    # Both attempts failed — never 500, return fallback
    msg = AIMessage(content=_FALLBACK)
    return {"answer": _FALLBACK, "messages": [msg]}
