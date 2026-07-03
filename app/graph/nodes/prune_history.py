from langchain_core.messages import RemoveMessage

from app.state import AgentState

# thread_id is stable per user (not per session — see app/channels/*.py), so
# AgentState.messages grows without bound across a user's entire lifetime.
# trim_messages() in generate.py/triage.py only limits what's sent to the LLM
# per call; it never shrinks what's persisted in the checkpoint. Prune here
# with a keep/trigger gap so this only fires in batches (~every 20 turns)
# instead of emitting a RemoveMessage on every single turn.
_KEEP_LAST = 40
_PRUNE_TRIGGER = 60


async def prune_history(state: AgentState) -> dict:
    messages = state["messages"]
    if len(messages) <= _PRUNE_TRIGGER:
        return {}

    to_remove = messages[: len(messages) - _KEEP_LAST]
    return {"messages": [RemoveMessage(id=m.id) for m in to_remove]}
