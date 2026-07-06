"""Unit tests for app.services.stt.transcribe()."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.stt import STTNotConfiguredError, transcribe


@pytest.fixture(autouse=True)
def _reset_client_singleton():
    """The module keeps a lazy singleton client — reset it around every test
    so one test's patched settings don't leak into the next via a stale client."""
    import app.services.stt as stt_module
    stt_module._client = None
    yield
    stt_module._client = None


def _mock_openai_client(text: str = "hola"):
    result = MagicMock()
    result.text = text
    client = MagicMock()
    client.audio.transcriptions.create = AsyncMock(return_value=result)
    return client


@pytest.mark.asyncio
async def test_raises_not_configured_when_api_key_missing():
    """No GROQ_API_KEY → STTNotConfiguredError, not a silent empty string."""
    with patch("app.services.stt.settings.groq_api_key", ""):
        with pytest.raises(STTNotConfiguredError):
            await transcribe(b"audio")


@pytest.mark.asyncio
async def test_language_hint_passed_to_groq():
    """settings.stt_language reaches the Groq call as the `language` kwarg."""
    client = _mock_openai_client("hola")
    with (
        patch("app.services.stt.settings.groq_api_key", "fake-key"),
        patch("app.services.stt.settings.stt_language", "es"),
        patch("app.services.stt._get_client", return_value=client),
    ):
        await transcribe(b"audio", "voice.ogg", "audio/ogg")
    _, kwargs = client.audio.transcriptions.create.call_args
    assert kwargs["language"] == "es"


@pytest.mark.asyncio
async def test_filename_and_mime_type_passed_through():
    """Caller-supplied filename/mime_type reach the Groq call unmodified —
    Telegram audio/video_note formats aren't silently coerced to ogg."""
    client = _mock_openai_client("hola")
    with (
        patch("app.services.stt.settings.groq_api_key", "fake-key"),
        patch("app.services.stt._get_client", return_value=client),
    ):
        await transcribe(b"audio-bytes", "audio.mp3", "audio/mpeg")
    _, kwargs = client.audio.transcriptions.create.call_args
    assert kwargs["file"] == ("audio.mp3", b"audio-bytes", "audio/mpeg")


@pytest.mark.asyncio
async def test_empty_transcription_returns_empty_string_not_error():
    """Groq reporting no speech detected is a valid response, not an exception —
    callers distinguish this from STT failure via the return value, not a catch."""
    client = _mock_openai_client("")
    with (
        patch("app.services.stt.settings.groq_api_key", "fake-key"),
        patch("app.services.stt._get_client", return_value=client),
    ):
        result = await transcribe(b"silence")
    assert result == ""


@pytest.mark.asyncio
async def test_client_is_reused_across_calls():
    """The Groq client is a lazy singleton — not re-instantiated per transcription."""
    with (
        patch("app.services.stt.settings.groq_api_key", "fake-key"),
        patch("app.services.stt.AsyncOpenAI") as mock_ctor,
    ):
        mock_ctor.return_value = _mock_openai_client("a")
        await transcribe(b"audio1")
        await transcribe(b"audio2")
    mock_ctor.assert_called_once()
