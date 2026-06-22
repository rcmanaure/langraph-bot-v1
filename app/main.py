import logging
import os
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from app.config import settings
from app.routes.admin import router as admin_router
from app.routes.operator import router as operator_router

logger = logging.getLogger(__name__)


def _setup_langsmith() -> None:
    """Bridge pydantic settings → os.environ so the LangSmith SDK sees them.

    pydantic-settings reads .env but does not populate os.environ; the SDK reads
    os.environ directly, so without this bridge local dev tracing silently no-ops.
    setdefault preserves values already set in the actual OS environment.
    """
    if settings.langchain_tracing_v2:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    if settings.langchain_api_key:
        os.environ.setdefault("LANGCHAIN_API_KEY", settings.langchain_api_key)
    if settings.langchain_project:
        os.environ.setdefault("LANGCHAIN_PROJECT", settings.langchain_project)
    if settings.langsmith_hide_inputs:
        os.environ.setdefault("LANGSMITH_HIDE_INPUTS", "true")
    if settings.langsmith_hide_outputs:
        os.environ.setdefault("LANGSMITH_HIDE_OUTPUTS", "true")


async def _cleanup_stuck_jobs() -> None:
    """Delete partial chunks and mark RUNNING/PENDING jobs as FAILED on startup.

    Prevents partial embedding corruption if the process was killed mid-indexing.
    The job_id FK on document_chunks makes this a targeted DELETE, not a full scan.
    """
    from sqlalchemy import text
    from app.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = await db.execute(
            text("SELECT id FROM index_jobs WHERE status IN ('RUNNING', 'PENDING')")
        )
        stuck = [str(r.id) for r in rows.fetchall()]
        if not stuck:
            return

        for job_id in stuck:
            await db.execute(
                text("DELETE FROM document_chunks WHERE job_id = :jid"),
                {"jid": job_id},
            )
        await db.execute(
            text("""
                UPDATE index_jobs
                   SET status = 'FAILED',
                       error_message = 'Startup cleanup: interrupted by server restart',
                       updated_at = now()
                 WHERE status IN ('RUNNING', 'PENDING')
            """)
        )
        await db.commit()
        logger.info("startup_cleanup_done stuck_jobs=%d", len(stuck))


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _cleanup_stuck_jobs()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    # psycopg3 needs plain postgresql:// (not +asyncpg)
    pg_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async with AsyncConnectionPool(
        conninfo=pg_url,
        max_size=settings.db_checkpoint_pool_size,
        kwargs={"autocommit": True},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        from app.graph.builder import build_graph

        app.state.graph = build_graph(checkpointer=checkpointer)
        logger.info("langgraph_ready")
        yield

    logger.info("shutdown_complete")


def _setup_sentry() -> None:
    if settings.sentry_dsn:
        sentry_sdk.init(dsn=settings.sentry_dsn, environment=settings.environment)


def create_app() -> FastAPI:
    _setup_langsmith()
    _setup_sentry()
    return FastAPI(title="LangGraph RAG Bot", lifespan=lifespan)


app = create_app()
app.include_router(operator_router)
app.include_router(admin_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
