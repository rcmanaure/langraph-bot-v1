import base64
import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 10 MB — shared cap for voice/audio/image downloads

VISION_UNCERTAIN = "__VISION_UNCERTAIN__"

_VISION_EXTRACT_PROMPT = (
    "Analiza esta imagen médica (orden de examen, informe, o solicitud de biopsia). "
    "Transcribe el nombre del procedimiento o examen EXACTAMENTE como aparece escrito en la "
    "imagen — no lo traduzcas a un sinónimo clínico ni asumas qué examen 'parecido' podría ser. "
    "Luego formula una pregunta de precio en español usando ese texto literal, por ejemplo: "
    "'¿Cuánto cuesta un examen de IGRA?' o '¿Cuál es el precio de una resección de tumor de mama?'. "
    f"Si el texto no es legible, está cortado, borroso, o hay varios exámenes distintos y no "
    f"puedes determinar cuál se pregunta, responde ÚNICAMENTE con: {VISION_UNCERTAIN}\n"
    "En cualquier otro caso responde ÚNICAMENTE con la pregunta, sin explicaciones adicionales."
)


async def extract_procedure_query(img_bytes: bytes, caption: str) -> str:
    """Vision-transcribe a medical order/exam image into a literal price question.

    Shared by every channel (Telegram, WhatsApp, ...) that accepts image uploads,
    so the anti-hallucination prompt (literal transcription, explicit uncertainty
    signal) stays identical across channels instead of drifting.
    """
    if not settings.openai_vision_model:
        raise RuntimeError("OPENAI_VISION_MODEL not configured")
    prompt = f"{caption}\n\n{_VISION_EXTRACT_PROMPT}" if caption else _VISION_EXTRACT_PROMPT
    img_b64 = base64.b64encode(img_bytes).decode()
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(
            f"{settings.openrouter_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
            json={
                "model": settings.openai_vision_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    ],
                }],
            },
        )
    if r.status_code != 200:
        logger.warning("vision_api_failed status=%d body=%s", r.status_code, r.text[:200])
        raise RuntimeError(f"Vision API returned {r.status_code}")
    return r.json()["choices"][0]["message"]["content"].strip()
