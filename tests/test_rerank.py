"""Unit tests for cross-encoder reranking — httpx is mocked, no network."""
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.rerank import rerank_chunks


def _chunks(n):
    return [{"content": f"Ítem número {i}"} for i in range(n)]


def _mock_client(results=None, status_code=200, http_error=False, side_effect=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {"results": results if results is not None else []}
    if http_error:
        request = httpx.Request("POST", "https://openrouter.ai/api/v1/rerank")
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"{status_code}", request=request, response=httpx.Response(status_code, request=request)
        )
    else:
        resp.raise_for_status.return_value = None

    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    if side_effect:
        client.post = AsyncMock(side_effect=side_effect)
    else:
        client.post = AsyncMock(return_value=resp)
    return client


def _result(index, score):
    return {"index": index, "relevance_score": score, "document": {"text": ""}}


@pytest.mark.asyncio
async def test_rerank_reorders_by_relevance_score():
    chunks = _chunks(5)
    mock_client = _mock_client(results=[_result(3, 0.9), _result(1, 0.7), _result(0, 0.5)])

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result == [chunks[3], chunks[1], chunks[0]]


@pytest.mark.asyncio
async def test_rerank_skips_when_fewer_chunks_than_top_k():
    """No point calling the API when there's nothing to actually filter/reorder
    beyond what's already there — should not even open an HTTP client."""
    chunks = _chunks(2)

    with patch("app.services.rerank.httpx.AsyncClient") as mock_async_client:
        result = await rerank_chunks("query", chunks, top_k=5)

    mock_async_client.assert_not_called()
    assert result == chunks


@pytest.mark.asyncio
async def test_rerank_disabled_via_config_falls_back_to_hybrid_order():
    chunks = _chunks(5)

    with (
        patch("app.services.rerank.settings.rerank_enabled", False),
        patch("app.services.rerank.httpx.AsyncClient") as mock_async_client,
    ):
        result = await rerank_chunks("query", chunks, top_k=3)

    mock_async_client.assert_not_called()
    assert result == chunks[:3]


@pytest.mark.asyncio
async def test_rerank_timeout_falls_back_to_hybrid_order():
    chunks = _chunks(5)
    mock_client = _mock_client(side_effect=httpx.TimeoutException("timed out"))

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result == chunks[:3]


@pytest.mark.asyncio
async def test_rerank_http_error_falls_back_generic_log(caplog):
    chunks = _chunks(5)
    mock_client = _mock_client(status_code=500, http_error=True)

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level("WARNING"):
            result = await rerank_chunks("query", chunks, top_k=3)

    assert result == chunks[:3]
    assert "rerank_failed" in caplog.text
    assert "rerank_rate_limited" not in caplog.text


@pytest.mark.asyncio
async def test_rerank_rate_limited_logs_distinctly_and_falls_back(caplog):
    chunks = _chunks(5)
    mock_client = _mock_client(status_code=429, http_error=True)

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        with caplog.at_level("WARNING"):
            result = await rerank_chunks("query", chunks, top_k=3)

    assert result == chunks[:3]
    assert "rerank_rate_limited" in caplog.text


@pytest.mark.asyncio
async def test_rerank_malformed_response_falls_back():
    chunks = _chunks(5)
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"unexpected": "shape"}  # missing "results" key
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=resp)

    with patch("app.services.rerank.httpx.AsyncClient", return_value=client):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result == chunks[:3]


@pytest.mark.asyncio
async def test_rerank_backfills_when_fewer_results_than_top_k():
    chunks = _chunks(5)
    mock_client = _mock_client(results=[_result(2, 0.9)])  # API only found one relevant item

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        result = await rerank_chunks("query", chunks, top_k=3)

    assert result[0] == chunks[2]
    assert len(result) == 3
    assert result[1] == chunks[0]  # backfilled in original hybrid order
    assert result[2] == chunks[1]


@pytest.mark.asyncio
async def test_rerank_ignores_out_of_range_indices():
    chunks = _chunks(3)
    mock_client = _mock_client(
        results=[_result(99, 0.9), _result(0, 0.8), _result(-1, 0.7), _result(1, 0.6)]
    )

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        result = await rerank_chunks("query", chunks, top_k=2)

    assert result == [chunks[0], chunks[1]]


@pytest.mark.asyncio
async def test_rerank_all_indices_invalid_falls_back_to_hybrid_order():
    chunks = _chunks(3)
    mock_client = _mock_client(results=[_result(99, 0.9), _result(-5, 0.8)])

    with patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client):
        result = await rerank_chunks("query", chunks, top_k=2)

    assert result == chunks[:2]


@pytest.mark.asyncio
async def test_rerank_api_request_shape():
    chunks = _chunks(5)
    mock_client = _mock_client(results=[_result(0, 0.9)])

    with (
        patch("app.services.rerank.httpx.AsyncClient", return_value=mock_client),
        patch("app.services.rerank.settings.rerank_model", "test/model:free"),
        patch("app.services.rerank.settings.openrouter_base_url", "https://openrouter.ai/api/v1"),
        patch("app.services.rerank.settings.openrouter_api_key", "test-key"),
    ):
        await rerank_chunks("what is the price?", chunks, top_k=3)

    mock_client.post.assert_awaited_once()
    args, kwargs = mock_client.post.call_args
    assert args[0] == "https://openrouter.ai/api/v1/rerank"
    assert kwargs["json"]["model"] == "test/model:free"
    assert kwargs["json"]["query"] == "what is the price?"
    assert kwargs["json"]["documents"] == [c["content"][:300] for c in chunks]
    assert kwargs["json"]["top_n"] == 3
    assert kwargs["headers"]["Authorization"] == "Bearer test-key"
