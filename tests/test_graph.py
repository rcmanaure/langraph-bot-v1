"""Graph compilation and routing logic tests."""
import pytest

from app.graph.builder import _route_after_validate, _route_triage, build_graph


def test_route_after_validate_blocked(base_state):
    base_state["blocked"] = True
    assert _route_after_validate(base_state) == "respond"


def test_route_after_validate_clean(base_state):
    assert _route_after_validate(base_state) == "retrieve"


@pytest.mark.parametrize("decision,expected", [
    ("rag", "generate"),
    ("catalog", "generate"),
    ("human", "interrupt_node"),
    ("off_topic", "respond"),
])
def test_route_triage(base_state, decision, expected):
    base_state["triage_decision"] = decision
    assert _route_triage(base_state) == expected


def test_route_triage_missing_defaults_to_rag(base_state):
    # no triage_decision key → defaults to "rag" → "generate"
    del base_state["triage_decision"]
    assert _route_triage(base_state) == "generate"


def test_build_graph_compiles():
    graph = build_graph(checkpointer=None)
    assert graph is not None


def test_graph_has_expected_nodes():
    graph = build_graph(checkpointer=None)
    nodes = set(graph.nodes)
    expected = {"validate", "retrieve", "triage", "generate", "validate_output", "interrupt_node", "respond"}
    assert expected.issubset(nodes)
