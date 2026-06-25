# Agent DNA — AgentState Schema Contract

**Schema version:** `1.0.0` (see `app/state.py:SCHEMA_VERSION`)

`AgentState` is the shared memory of the LangGraph agent for a single conversation turn.
It is the **only** data structure that flows through graph edges — treat it as a versioned API.

## Fields

| Field | Type | Owner | Notes |
|---|---|---|---|
| `tenant_id` | `str` | Channel layer | Slug only. Full `TenantConfig` (secrets) is **never** placed here — it is not checkpointed. |
| `thread_id` | `str` | Channel layer | Format: `tenant:{slug}:user:{user_id}:channel:{channel}(:vN)`. Append `:vN` when the schema breaks and old checkpoints must be ignored. |
| `messages` | `list[BaseMessage]` | `add_messages` reducer | LangGraph appends; do not assign directly. |
| `retrieved_chunks` | `list[dict]` | `retrieve` node | Cleared each turn by the retrieve node before writing. |
| `triage_decision` | `str` | `triage` node | Enum-like: `"rag"` \| `"catalog"` \| `"human"` \| `"off_topic"`. |
| `answer` | `str` | `generate` node | Final response string written by generate; read by channel layer. |
| `blocked` | `bool` (optional) | `validate` node | Set to `True` on prompt-injection detection. Short-circuits the graph before triage. |

## Invariants

1. **Secrets never in state.** `tenant_id` is a slug resolved to credentials at the node boundary, not carried in state.
2. **`answer` is the canonical output.** Channel adapters check `answer` first, then fall back to `messages[-1].content` for compatibility.
3. **`thread_id` scopes checkpoints.** Each `(tenant, user, channel)` triplet has an isolated conversation history. Adding `:vN` forces a clean slate without deleting old data.
4. **`blocked` gates execution.** Any node can set `blocked = True`; the validate node checks it before routing.

## Versioning

Bump `SCHEMA_VERSION` in `app/state.py` when:
- A **field is renamed or removed** (breaking — also bump `:vN` in thread_id format)
- A **field is added with a required value** (breaking)
- A **field is added with `NotRequired`** (non-breaking — no version bump needed)

## Adding a Field

1. Add to `AgentState` TypedDict in `app/state.py` as `NotRequired[T]` if optional.
2. Update this document.
3. If breaking: bump `SCHEMA_VERSION` and update `thread_id` format docs above.
