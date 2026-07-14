"""Shared bootstrap for the LangGraph runtime: Postgres connection pool,
checkpointer, long-term memory store, and compiled graph.

Used by the FastAPI lifespan (app/main.py) today, and will be used by the
background task worker once it exists (docs/plans/2026-07-06-voice-scalability-plan.md,
Fase 2.2) — a single place to tune pool sizing instead of two setup paths
that can silently drift apart.
"""
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from app.config import settings


@dataclass
class Runtime:
    pool: AsyncConnectionPool
    checkpointer: AsyncPostgresSaver
    store: AsyncPostgresStore
    graph: Any  # CompiledStateGraph — untyped to avoid a hard langgraph.graph.state import


@asynccontextmanager
async def build_runtime():
    """Open the Postgres pool, run checkpointer/store setup, and compile the
    graph. Yields a Runtime; the pool closes when the `async with` block exits."""
    from app.graph.builder import build_graph

    # psycopg3 needs plain postgresql:// (not +asyncpg)
    pg_url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    async with AsyncConnectionPool(
        conninfo=pg_url,
        max_size=settings.db_checkpoint_pool_size,
        kwargs={"autocommit": True},
    ) as pool:
        checkpointer = AsyncPostgresSaver(pool)
        await checkpointer.setup()

        # Same pool as the checkpointer — long-term (cross-thread) user profile
        # memory, separate from the per-thread conversation state above.
        store = AsyncPostgresStore(pool)
        await store.setup()

        graph = build_graph(checkpointer=checkpointer, store=store)
        yield Runtime(pool=pool, checkpointer=checkpointer, store=store, graph=graph)
