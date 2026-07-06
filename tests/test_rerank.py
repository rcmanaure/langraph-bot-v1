"""Unit tests for LLM-based reranking — the chat LLM is mocked, no network."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.schemas.rerank import RerankResult
from app.services.rerank import rerank_chunks


def _chunks(n):
    return [{"content": f"Ítem número {i}"} for i in range(n)]


def _mock_llm(ranked_indices=None, side_effect=None):
    mock_llm = MagicMock()
    mock_structured = AsyncMock()
    if side_effect:
        mock_structured.ainvoke = AsyncMock(side_effect=side_effect)
    else:
        mock_structured.ainvoke = AsyncMock(return_value=RerankResult(ranked_indices=ranked_indices))
    mock_llm.with_structured_output.return_value = mock_structured
    return mock_llm


@pytest.mark.asyncio
async def test_rerank_reorders_by_llm_indices():
    chunks = _chunks(5)
    mock_llm = _mock_llm(ranked_indices=[3, 1, 0])

    with patch("app.services.rerank.get_chat_llm", return_value=mock_llm):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result == [chunks[3], chunks[1], chunks[0]]


@pytest.mark.asyncio
async def test_rerank_skips_when_fewer_chunks_than_top_k():
    """No point calling the LLM when there's nothing to actually filter/reorder
    beyond what's already there — should not even construct the LLM."""
    chunks = _chunks(2)

    with patch("app.services.rerank.get_chat_llm") as mock_get_llm:
        result = await rerank_chunks("query", chunks, top_k=5)

    mock_get_llm.assert_not_called()
    assert result == chunks


@pytest.mark.asyncio
async def test_rerank_disabled_via_config_falls_back_to_hybrid_order():
    chunks = _chunks(5)

    with (
        patch("app.services.rerank.settings.rerank_enabled", False),
        patch("app.services.rerank.get_chat_llm") as mock_get_llm,
    ):
        result = await rerank_chunks("query", chunks, top_k=3)

    mock_get_llm.assert_not_called()
    assert result == chunks[:3]


@pytest.mark.asyncio
async def test_rerank_llm_failure_falls_back_to_hybrid_order():
    chunks = _chunks(5)
    mock_llm = _mock_llm(side_effect=Exception("rate limited"))

    with patch("app.services.rerank.get_chat_llm", return_value=mock_llm):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result == chunks[:3]


@pytest.mark.asyncio
async def test_rerank_backfills_when_model_returns_fewer_than_top_k():
    chunks = _chunks(5)
    mock_llm = _mock_llm(ranked_indices=[2])  # model only found one relevant item

    with patch("app.services.rerank.get_chat_llm", return_value=mock_llm):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result[0] == chunks[2]
    assert len(result) == 3
    assert result[1] == chunks[0]  # backfilled in original hybrid order
    assert result[2] == chunks[1]


@pytest.mark.asyncio
async def test_rerank_ignores_out_of_range_indices():
    chunks = _chunks(3)
    mock_llm = _mock_llm(ranked_indices=[99, 0, -1, 1])

    with patch("app.services.rerank.get_chat_llm", return_value=mock_llm):
        result = await rerank_chunks("query", chunks, top_k=2)

    assert result == [chunks[0], chunks[1]]


@pytest.mark.asyncio
async def test_rerank_all_indices_invalid_falls_back_to_hybrid_order():
    chunks = _chunks(3)
    mock_llm = _mock_llm(ranked_indices=[99, -5])

    with patch("app.services.rerank.get_chat_llm", return_value=mock_llm):
        result = await rerank_chunks("query", chunks, top_k=2)

    assert result == chunks[:2]
