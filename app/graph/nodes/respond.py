from app.state import AgentState


async def respond(state: AgentState) -> dict:
    # ponytail: answer already in state["messages"][-1]; channel handlers (T10) read it there
    return {}
