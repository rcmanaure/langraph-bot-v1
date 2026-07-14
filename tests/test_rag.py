"""Unit tests for the hybrid (dense + keyword) retrieval query — DB and
embeddings are mocked."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.services.rag import cap_chunks_to_tokens, retrieve_chunks, token_counter


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


# ---------------------------------------------------------------------------
# cap_chunks_to_tokens / token_counter — used by retrieve(), generate(), and
# triage() to keep prompts under budget; untested until now.
# ---------------------------------------------------------------------------

def test_cap_chunks_to_tokens_empty_list_returns_empty():
    assert cap_chunks_to_tokens([], max_tokens=1000) == []


def test_cap_chunks_to_tokens_keeps_all_when_under_budget():
    chunks = [{"content": "short chunk one"}, {"content": "short chunk two"}]
    assert cap_chunks_to_tokens(chunks, max_tokens=1000) == chunks


def test_cap_chunks_to_tokens_drops_oversized_first_chunk_entirely():
    """A single chunk larger than the whole budget is dropped, not truncated
    to fit — cap_chunks_to_tokens only ever keeps or skips whole chunks."""
    huge_chunk = {"content": "palabra " * 2000}  # way over any reasonable budget
    chunks = [huge_chunk, {"content": "small chunk"}]

    result = cap_chunks_to_tokens(chunks, max_tokens=10)

    assert result == []  # the loop breaks on the first chunk, never reaches the second


def test_cap_chunks_to_tokens_stops_at_first_chunk_that_would_exceed():
    """Greedy/order-dependent: once a chunk would push the running total over
    budget, iteration stops — later chunks that might individually fit are
    never considered, even if they're smaller than the one that broke the loop."""
    chunks = [
        {"content": "a"},                 # tiny, fits
        {"content": "palabra " * 2000},    # huge, breaks the loop
        {"content": "b"},                 # tiny, would also fit, but never reached
    ]

    result = cap_chunks_to_tokens(chunks, max_tokens=10)

    assert result == [chunks[0]]


def test_cap_chunks_to_tokens_exact_budget_boundary():
    """A chunk landing exactly on the budget is kept (strict > check, not >=)."""
    chunk = {"content": "hola"}
    exact_tokens = token_counter([HumanMessage(content="hola")])

    result = cap_chunks_to_tokens([chunk], max_tokens=exact_tokens)

    assert result == [chunk]


def test_token_counter_empty_list_returns_zero():
    assert token_counter([]) == 0


def test_token_counter_ignores_non_string_content():
    """Multimodal message content (list of content blocks, e.g. image_url
    parts) isn't text — token_counter must not crash or mis-count on it."""
    text_msg = HumanMessage(content="hola mundo")
    multimodal_msg = HumanMessage(content=[{"type": "image_url", "image_url": {"url": "data:..."}}])

    text_only = token_counter([text_msg])
    with_multimodal = token_counter([text_msg, multimodal_msg])

    assert with_multimodal == text_only  # the non-string message contributes 0


def test_token_counter_counts_ai_and_human_messages():
    msgs = [HumanMessage(content="hola"), AIMessage(content="hola, como estas")]
    assert token_counter(msgs) == token_counter([msgs[0]]) + token_counter([msgs[1]])
