"""Unit tests for the hybrid (dense + keyword) retrieval query — DB and
embeddings are mocked."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.rag import retrieve_chunks


def _row(content, source, page, similarity):
    row = MagicMock()
    row.content = content
    row.source = source
    row.page = page
    row.similarity = similarity
    return row


def _mock_db(rows):
    mock_result = MagicMock()
    mock_result.fetchall.return_value = rows
    mock_db = MagicMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    return mock_db


def _mock_embeddings():
    mock_embeddings = MagicMock()
    mock_embeddings.aembed_query = AsyncMock(return_value=[0.1] * 1536)
    return mock_embeddings


@pytest.mark.asyncio
async def test_retrieve_chunks_builds_dicts_from_fused_rows():
    rows = [
        _row("Ítem A", "catalog.jsonl:A", 1, 0.834521),
        _row("Ítem B", "catalog.jsonl:B", 2, 0.412),
    ]
    mock_db = _mock_db(rows)

    with patch("app.services.rag.get_embeddings", return_value=_mock_embeddings()):
        chunks = await retrieve_chunks(mock_db, "cuanto cuesta el item A", "tenant-1")

    assert chunks == [
        {"content": "Ítem A", "source": "catalog.jsonl:A", "page": 1, "similarity": 0.835},
        {"content": "Ítem B", "source": "catalog.jsonl:B", "page": 2, "similarity": 0.412},
    ]


@pytest.mark.asyncio
async def test_retrieve_chunks_query_fuses_dense_and_keyword_search():
    """The exact-match gap (product codes, item names) is the reason hybrid
    search exists — verify the query actually does keyword search + RRF
    fusion, not just vector-only dressed up."""
    mock_db = _mock_db([])

    with patch("app.services.rag.get_embeddings", return_value=_mock_embeddings()):
        await retrieve_chunks(mock_db, "COD-123", "tenant-1")

    # First two calls are the SET LOCAL hnsw.* statements; the fused query is last.
    final_call = mock_db.execute.await_args_list[-1]
    sql_text = str(final_call.args[0])
    params = final_call.args[1]

    assert "websearch_to_tsquery" in sql_text
    assert "content_tsv" in sql_text
    assert "FULL OUTER JOIN" in sql_text
    assert "embedding <=>" in sql_text
    assert params["q"] == "COD-123"
    assert params["cand_k"] > 0
    assert params["rrf_k"] > 0


@pytest.mark.asyncio
async def test_retrieve_chunks_empty_result_returns_empty_list():
    mock_db = _mock_db([])

    with patch("app.services.rag.get_embeddings", return_value=_mock_embeddings()):
        chunks = await retrieve_chunks(mock_db, "algo que no existe en el catalogo", "tenant-1")

    assert chunks == []
