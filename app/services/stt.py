import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# Groq is OpenAI-compatible — reuse the openai client, no extra dep needed
_GROQ_BASE = "https://api.groq.com/openai/v1"
_MODEL = "whisper-large-v3"


async def transcribe(audio_bytes: bytes, filename: str = "audio.ogg") -> str:
    if not settings.groq_api_key:
        logger.warning("stt_skipped: GROQ_API_KEY not set")
        return ""
    client = AsyncOpenAI(api_key=settings.groq_api_key, base_url=_GROQ_BASE)
    result = await client.audio.transcriptions.create(
        model=_MODEL,
        file=(filename, audio_bytes, "audio/ogg"),
    )
    return result.text or ""
