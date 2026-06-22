import logging
import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from sqlalchemy import text

from app.db import AsyncSessionLocal
from app.state import AgentState

logger = logging.getLogger(__name__)


def _parse_part(thread_id: str, key: str) -> str:
    parts = thread_id.split(":")
    try:
        return parts[parts.index(key) + 1]
    except (ValueError, IndexError):
        return "unknown"


async def interrupt_node(state: AgentState) -> dict:
    thread_id = state.get("thread_id", "")

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    INSERT INTO conversation_audit
                        (id, tenant_id, thread_id, user_id, channel,
                         interrupt_started_at, created_at)
                    SELECT
                        :id,
                        t.id,
                        :thread,
                        :user_id,
                        :channel,
                        :now,
                        :now
                    FROM tenants t WHERE t.slug = :slug
                """),
                {
                    "id": str(uuid.uuid4()),
                    "thread": thread_id,
                    "user_id": _parse_part(thread_id, "user"),
                    "channel": _parse_part(thread_id, "channel"),
                    "now": datetime.now(timezone.utc),
                    "slug": state["tenant_id"],
                },
            )
            await db.commit()
    except Exception as exc:
        logger.warning("interrupt_audit_failed thread=%s error=%s", thread_id, exc)

    # Suspend graph — resumes via POST /operator/resume/{thread_id}
    # with Command(resume=operator_text)
    operator_answer: str = interrupt({"type": "needs_human", "thread_id": thread_id})

    return {
        "answer": operator_answer,
        "messages": [AIMessage(content=operator_answer)],
    }
