from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import text

from app.auth import verify_operator_key
from app.db import AsyncSessionLocal
from app.services.security import validate_thread_id

_limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/operator", tags=["operator"])


class ResumeRequest(BaseModel):
    text: str


@router.post("/resume/{thread_id}")
@_limiter.limit("20/minute")
async def resume(
    thread_id: str,
    body: ResumeRequest,
    request: Request,
    _: None = Depends(verify_operator_key),
):
    if not validate_thread_id(thread_id):
        raise HTTPException(status_code=422, detail="Invalid thread_id format")

    from langgraph.types import Command

    graph = request.app.state.graph
    config = {"configurable": {"thread_id": thread_id}}

    result = await graph.ainvoke(Command(resume=body.text), config=config)

    # Mark interrupt resolved in audit
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text("""
                    UPDATE conversation_audit
                       SET expired_at = :now,
                           bot_response = :resp
                     WHERE thread_id = :tid
                       AND expired_at IS NULL
                       AND interrupt_started_at IS NOT NULL
                """),
                {
                    "now": datetime.now(timezone.utc),
                    "resp": body.text,
                    "tid": thread_id,
                },
            )
            await db.commit()
    except Exception as exc:
        # Non-fatal — audit failure must not block the operator response
        import logging
        logging.getLogger(__name__).warning("audit_update_failed thread=%s err=%s", thread_id, exc)

    answer = body.text
    if result and isinstance(result, dict):
        answer = result.get("answer") or answer

    return {"status": "resumed", "thread_id": thread_id, "answer": answer}


@router.get("/pending")
async def list_pending(
    _: None = Depends(verify_operator_key),
):
    """List threads currently waiting for operator response."""
    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text("""
                SELECT thread_id, user_id, channel, user_message, interrupt_started_at
                  FROM conversation_audit
                 WHERE expired_at IS NULL
                   AND interrupt_started_at IS NOT NULL
                 ORDER BY interrupt_started_at
            """)
        )
        return [dict(r._mapping) for r in rows.fetchall()]
