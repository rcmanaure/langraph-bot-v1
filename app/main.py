import logging
from contextlib import asynccontextmanager

import sentry_sdk
from fastapi import FastAPI

from app.config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    _setup_sentry()
    return FastAPI(title="LangGraph RAG Bot", lifespan=lifespan)


app = create_app()


@app.get("/health")
async def health():
    return {"status": "ok"}
