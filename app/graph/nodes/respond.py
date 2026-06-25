from app.state import AgentState


async def respond(state: AgentState) -> dict:
    # Terminal node — no state mutation; channel handlers read state["answer"] directly.
    return {}
