import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Groq is OpenAI-compatible — reuse the openai client, no extra dep needed
_GROQ_BASE = "https://api.groq.com/openai/v1"
_MODEL = "whisper-large-v3"

_client: AsyncOpenAI | None = None


class STTNotConfiguredError(RuntimeError):
    """Raised when GROQ_API_KEY is unset — distinct from a transcription failure
    so callers can send a "feature not enabled" message instead of a generic error."""


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.groq_api_key, base_url=_GROQ_BASE)
    return _client


async def transcribe(audio_bytes: bytes, filename: str = "audio.ogg", mime_type: str = "audio/ogg") -> str:
    """Transcribe audio via Groq Whisper.

    Raises STTNotConfiguredError if GROQ_API_KEY is unset, or propagates the
    underlying API exception on failure — callers must handle both to avoid
    going silent on the user. Returns "" only when Groq itself reports no
    speech detected, a valid (non-error) response.
    """
    if not settings.groq_api_key:
        logger.error("stt_not_configured: GROQ_API_KEY not set")
        raise STTNotConfiguredError("GROQ_API_KEY not set")
    client = _get_client()
    result = await client.audio.transcriptions.create(
        model=_MODEL,
        file=(filename, audio_bytes, mime_type),
        language=settings.stt_language,
    )
    return result.text or ""
