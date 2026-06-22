import hashlib
import hmac
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text

from app.config import settings
from app.db import AsyncSessionLocal

router = APIRouter(prefix="/operator", tags=["operator"])

# thread format: tenant:{slug}:user:{id}:channel:(telegram|whatsapp)(:vN)?
_THREAD_RE = re.compile(
    r"^tenant:[a-z0-9-]+:user:[0-9]+:channel:(telegram|whatsapp)(:v[0-9]+)?$"
)


class ResumeRequest(BaseModel):
    text: str


def _verify_operator_key(x_operator_key: str = Header(...)) -> None:
    # ponytail: uses SECRET_KEY for now — T8 replaces with dedicated operator tokens
    expected = hashlib.sha256(settings.secret_key.encode()).hexdigest()
    if not hmac.compare_digest(x_operator_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.post("/resume/{thread_id}")
async def resume(
    thread_id: str,
    body: ResumeRequest,
    request: Request,
    _: None = Depends(_verify_operator_key),
):
    if not _THREAD_RE.match(thread_id):
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
    _: None = Depends(_verify_operator_key),
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
