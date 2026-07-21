import asyncio
import logging
import time

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings
from app.services.embedding_cache import CachedEmbeddings

logger = logging.getLogger(__name__)

DEFAULT_RETRY_MAX_ATTEMPTS = 2
DEFAULT_RETRY_BASE_DELAY = 1.5  # seconds


async def call_with_retry(
    fn, *args, is_retryable, max_retries: int = DEFAULT_RETRY_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_RETRY_BASE_DELAY, **kwargs,
):
    """Retry an async callable with exponential backoff when is_retryable(exc)
    is True. Shared shape for any transient-error retry (originally
    vision.py's OpenAI 429 retry; also used for Google API 429s) — the
    predicate is what's provider-specific, not the loop."""
    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except Exception as exc:
            if not is_retryable(exc) or attempt == max_retries:
                raise
            delay = base_delay * (2**attempt)
            logger.warning("retryable_error attempt=%d retrying_in=%.1fs error=%s", attempt + 1, delay, exc)
            await asyncio.sleep(delay)


def call_with_retry_sync(
    fn, *args, is_retryable, max_retries: int = DEFAULT_RETRY_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_RETRY_BASE_DELAY, **kwargs,
):
    """Sync counterpart of call_with_retry — google-api-python-client's calls
    are blocking, not async, so they can't share the async loop above.
    ponytail: kept as a separate tiny function rather than unifying via a
    thread-pool wrapper; upgrade to asyncio.to_thread() if these blocking
    calls are found to stall the event loop under load."""
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            if not is_retryable(exc) or attempt == max_retries:
                raise
            delay = base_delay * (2**attempt)
            logger.warning("retryable_error attempt=%d retrying_in=%.1fs error=%s", attempt + 1, delay, exc)
            time.sleep(delay)


def get_chat_llm(fallback: bool = False) -> ChatOpenAI:
    model = (settings.openai_fallback_model if fallback else None) or settings.openai_model
    return ChatOpenAI(
        model=model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers={"HTTP-Referer": f"https://{settings.app_domain}"},
        timeout=60,
    )


def get_vision_llm() -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.openai_vision_model,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_headers={"HTTP-Referer": f"https://{settings.app_domain}"},
        timeout=60,
    )


def get_openrouter_headers() -> dict:
    """Auth + referer headers for raw HTTP calls to OpenRouter endpoints that
    aren't chat-completions (e.g. /rerank) and so can't go through ChatOpenAI."""
    return {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        "HTTP-Referer": f"https://{settings.app_domain}",
    }


def get_embeddings() -> CachedEmbeddings:
    underlying = OpenAIEmbeddings(
        model=settings.embedding_model,
        api_key=settings.effective_embedding_api_key,
        base_url=settings.effective_embedding_base_url,
        dimensions=settings.embedding_dim,
    )
    return CachedEmbeddings(underlying)
