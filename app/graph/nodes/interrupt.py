import logging
import uuid
from datetime import datetime, timezone

from langchain_core.messages import AIMessage
from langgraph.types import interrupt
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.db import AsyncSessionLocal
from app.graph.thread import parse_thread_part
from app.state import AgentState

logger = logging.getLogger(__name__)


async def interrupt_node(state: AgentState) -> dict:
    thread_id = state.get("thread_id", "")

    # interrupt() re-runs this node from the top on every resume, so the audit
    # insert must be idempotent: only write it the first time this thread hits
    # an open interrupt, not on each resume replay.
    try:
        async with AsyncSessionLocal() as db:
            existing = (await db.execute(
                text("""
                    SELECT 1 FROM conversation_audit
                     WHERE thread_id = :thread
                       AND expired_at IS NULL
                       AND interrupt_started_at IS NOT NULL
                """),
                {"thread": thread_id},
            )).first()

            if not existing:
                try:
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
                            "user_id": parse_thread_part(thread_id, "user"),
                            "channel": parse_thread_part(thread_id, "channel"),
                            "now": datetime.now(timezone.utc),
                            "slug": state["tenant_id"],
                        },
                    )
                    await db.commit()
                except IntegrityError:
                    # Lost the race to a concurrent invocation of the same thread
                    # (e.g. a redelivered webhook) that inserted first — benign,
                    # the unique partial index guarantees only one row exists.
                    await db.rollback()
    except Exception as exc:
        logger.warning("interrupt_audit_failed thread=%s error=%s", thread_id, exc)

    # Suspend graph — resumes via POST /operator/resume/{thread_id}
    # with Command(resume=operator_text)
    operator_answer: str = interrupt({"type": "needs_human", "thread_id": thread_id})

    return {
        "answer": operator_answer,
        "messages": [AIMessage(content=operator_answer)],
    }
