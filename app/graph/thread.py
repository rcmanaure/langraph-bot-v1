from app.state import AgentState


def parse_thread_part(thread_id: str, key: str) -> str:
    """Extract a component from thread_id (format tenant:{slug}:user:{id}:channel:{channel})."""
    parts = thread_id.split(":")
    try:
        return parts[parts.index(key) + 1]
    except (ValueError, IndexError):
        return "unknown"


def profile_namespace(state: AgentState) -> tuple[str, str]:
    """Store namespace for this user's long-term profile: (tenant_slug, "channel:user_id")."""
    thread_id = state.get("thread_id", "")
    channel = parse_thread_part(thread_id, "channel")
    user_id = parse_thread_part(thread_id, "user")
    return (state.get("tenant_id", ""), f"{channel}:{user_id}")
