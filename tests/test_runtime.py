"""Unit tests for app.runtime.build_runtime() — the shared bootstrap factory
used by both the FastAPI lifespan and (later) the background task worker."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.runtime import build_runtime


@pytest.mark.asyncio
async def test_build_runtime_wires_pool_checkpointer_store_and_graph():
    pool_instance = MagicMock()
    pool_cm = AsyncMock()
    pool_cm.__aenter__ = AsyncMock(return_value=pool_instance)
    pool_cm.__aexit__ = AsyncMock(return_value=None)

    checkpointer_instance = MagicMock()
    checkpointer_instance.setup = AsyncMock()

    store_instance = MagicMock()
    store_instance.setup = AsyncMock()

    sentinel_graph = MagicMock()

    with (
        patch("app.runtime.AsyncConnectionPool", return_value=pool_cm) as mock_pool_ctor,
        patch("app.runtime.AsyncPostgresSaver", return_value=checkpointer_instance) as mock_checkpointer_ctor,
        patch("app.runtime.AsyncPostgresStore", return_value=store_instance) as mock_store_ctor,
        patch("app.graph.builder.build_graph", return_value=sentinel_graph) as mock_build_graph,
        patch("app.runtime.settings.database_url", "postgresql+asyncpg://u:p@host/db"),
        patch("app.runtime.settings.db_checkpoint_pool_size", 7),
    ):
        async with build_runtime() as runtime:
            assert runtime.pool is pool_instance
            assert runtime.checkpointer is checkpointer_instance
            assert runtime.store is store_instance
            assert runtime.graph is sentinel_graph

    # psycopg3 needs plain postgresql:// (not +asyncpg)
    _, kwargs = mock_pool_ctor.call_args
    assert kwargs["conninfo"] == "postgresql://u:p@host/db"
    assert kwargs["max_size"] == 7

    mock_checkpointer_ctor.assert_called_once_with(pool_instance)
    mock_store_ctor.assert_called_once_with(pool_instance)
    checkpointer_instance.setup.assert_awaited_once()
    store_instance.setup.assert_awaited_once()
    mock_build_graph.assert_called_once_with(checkpointer=checkpointer_instance, store=store_instance)
    pool_cm.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_pool_closes_even_when_setup_raises():
    """A failure mid-bootstrap (e.g. checkpointer.setup() can't reach Postgres)
    must not leak the connection pool — __aexit__ still runs on exception."""
    pool_instance = MagicMock()
    pool_cm = AsyncMock()
    pool_cm.__aenter__ = AsyncMock(return_value=pool_instance)
    pool_cm.__aexit__ = AsyncMock(return_value=None)

    checkpointer_instance = MagicMock()
    checkpointer_instance.setup = AsyncMock(side_effect=RuntimeError("db unreachable"))

    with (
        patch("app.runtime.AsyncConnectionPool", return_value=pool_cm),
        patch("app.runtime.AsyncPostgresSaver", return_value=checkpointer_instance),
        patch("app.runtime.AsyncPostgresStore", return_value=MagicMock()),
    ):
        with pytest.raises(RuntimeError, match="db unreachable"):
            async with build_runtime():
                pass

    pool_cm.__aexit__.assert_awaited_once()
