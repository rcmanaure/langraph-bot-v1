# ADR-001: LangGraph as the Agent Orchestration Engine

**Status:** Accepted  
**Date:** 2025-01

## Context

The bot needs a stateful, multi-step agent: inbound message → validate → triage → (RAG retrieval + generate) or (catalog lookup) or (handoff to human). Each path has different node dependencies. Conversation history must survive across requests. Multiple tenants share one process.

## Decision

Use **LangGraph `StateGraph`** with `AgentState` as the shared state TypedDict and `AsyncPostgresSaver` for checkpoint persistence.

## Alternatives Considered

| Option | Why Rejected |
|---|---|
| Single `LangChain` chain | No native branching; routing requires custom conditional logic that reproduces what LangGraph provides |
| Custom `asyncio` pipeline | Checkpointing, retries, and graph visualization require re-implementation from scratch |
| Crew AI / AutoGen | Agent-to-agent coordination is unnecessary overhead for a single-agent RAG bot |

## Consequences

**Positive:**
- Built-in `AsyncPostgresSaver` checkpointer: conversation history is persistent and crash-safe
- `StateGraph` makes routing logic auditable as a directed graph (visualizable with `graph.get_graph().draw_mermaid()`)
- `ainvoke` is async-native; fits FastAPI without thread bridging

**Negative:**
- LangGraph API surface changes frequently across minor versions; upgrade requires testing
- Graph compilation (`graph.compile()`) happens at startup — a bad graph definition silently panics at boot, not at request time
- `AgentState` TypedDict becomes a shared contract; field changes must be versioned (see `app/state.py:SCHEMA_VERSION`)
