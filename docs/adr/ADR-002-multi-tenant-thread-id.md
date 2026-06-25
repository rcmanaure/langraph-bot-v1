# ADR-002: Multi-Tenant Isolation via thread_id, Single Graph Instance

**Status:** Accepted  
**Date:** 2025-01

## Context

The system serves multiple tenants from a single deployed instance. Each tenant has its own document corpus, bot token, and user base. Conversation history must be isolated between tenants and between users of the same tenant.

## Decision

Run **one compiled `StateGraph` per process**. Isolate tenants and users via the `thread_id` key passed to `graph.ainvoke(config={"configurable": {"thread_id": ...}})`.

Thread ID format: `tenant:{slug}:user:{user_id}:channel:{channel}(:vN)`

- `tenant_id` (slug) is placed in `AgentState` so nodes can load tenant-specific config
- Full `TenantConfig` (bot tokens, secrets) is **never** placed in `AgentState` — it is not checkpointed

## Alternatives Considered

| Option | Why Rejected |
|---|---|
| Separate compiled graph per tenant | Linear memory cost; recompilation on tenant add/remove; complicates deployment |
| Separate OS process per tenant | Too heavy for a 5-tenant SaaS; overkill until proven necessary |
| Database-level row isolation in graph | LangGraph's checkpointer already uses `thread_id` as the isolation key; duplicating at DB row level is redundant |

## Consequences

**Positive:**
- One graph instance handles all tenants; memory footprint is O(1) in tenant count
- `thread_id` doubles as the LangGraph checkpoint key — no additional mapping needed
- `:vN` suffix convention allows schema migrations without deleting old checkpoints

**Negative:**
- A bug in one tenant's graph execution runs in the same process as others (no hard fault boundary)
- Tenant-specific LLM configuration (model overrides) must be loaded inside nodes via `tenant_id`, not at compile time
- `thread_id` must be treated as sensitive: leaking it gives access to another user's conversation history
