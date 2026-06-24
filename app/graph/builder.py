from langgraph.graph import END, START, StateGraph

from app.graph.nodes.generate import generate
from app.graph.nodes.interrupt import interrupt_node
from app.graph.nodes.respond import respond
from app.graph.nodes.retrieve import retrieve
from app.graph.nodes.triage import triage
from app.graph.nodes.validate import validate
from app.graph.nodes.validate_output import validate_output
from app.state import AgentState


def _route_after_validate(state: AgentState) -> str:
    return "respond" if state.get("blocked") else "retrieve"


def _route_triage(state: AgentState) -> str:
    d = state.get("triage_decision", "rag")
    if d in ("rag", "catalog", "off_topic"):
        return "generate"
    if d == "human":
        return "interrupt_node"
    return "generate"


def build_graph(checkpointer=None):
    g = StateGraph(AgentState)

    g.add_node("validate", validate)
    g.add_node("retrieve", retrieve)
    g.add_node("triage", triage)
    g.add_node("generate", generate)
    g.add_node("validate_output", validate_output)
    g.add_node("interrupt_node", interrupt_node)
    g.add_node("respond", respond)

    g.add_edge(START, "validate")
    g.add_conditional_edges(
        "validate",
        _route_after_validate,
        {"retrieve": "retrieve", "respond": "respond"},
    )
    g.add_edge("retrieve", "triage")
    g.add_conditional_edges(
        "triage",
        _route_triage,
        {"generate": "generate", "interrupt_node": "interrupt_node", "respond": "respond"},
    )
    g.add_edge("generate", "validate_output")
    g.add_edge("validate_output", "respond")
    g.add_edge("interrupt_node", "respond")
    g.add_edge("respond", END)

    return g.compile(checkpointer=checkpointer)
