import base64
import json
import logging
import re

from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from app.config import settings
from app.schemas.vision import VisionExtraction, VisionVerification
from app.services.llm import get_vision_llm

logger = logging.getLogger(__name__)

MAX_MEDIA_BYTES = 10 * 1024 * 1024  # 10 MB — shared cap for voice/audio/image downloads

VISION_UNCERTAIN = "__VISION_UNCERTAIN__"

_VISION_EXTRACT_PROMPT = """\
Analiza esta imagen médica (orden de examen, informe, o solicitud de biopsia).

Marca is_legible=true SOLO si podés leer con certeza el nombre del procedimiento o \
examen escrito en la imagen, letra por letra. Si el texto está borroso, cortado, \
hay varios exámenes distintos y no sabés cuál se pregunta, o tenés la MÍNIMA duda \
sobre qué dice — marcá is_legible=false y dejá price_question vacío. NUNCA adivines \
ni asumas un examen "parecido" solo porque el contexto médico te resulte familiar.

Si is_legible=true, escribí en price_question una pregunta de precio en español \
usando el texto EXACTO que leíste en la imagen, por ejemplo: "¿Cuánto cuesta un \
examen de IGRA?" o "¿Cuál es el precio de una resección de tumor de mama?". No \
traduzcas a un sinónimo clínico ni asumas qué examen "similar" podría ser.
"""

_EXTRACT_JSON_SUFFIX = (
    '\nReply ONLY with JSON: {"is_legible": <true|false>, "price_question": <string or null>}'
)

_VERIFY_PROMPT_TEMPLATE = """\
Mirá esta imagen otra vez, con atención. ¿Aparece literalmente escrito en la imagen \
el siguiente texto, o una variante muy cercana del mismo procedimiento/examen?

Texto a verificar: "{claim}"

Marcá text_visible=true SOLO si podés señalar con certeza dónde en la imagen aparece \
ese texto o uno equivalente. Si no lo ves, no estás seguro, o la imagen no contiene \
ese texto, marcá text_visible=false. No asumas por contexto médico general — esto es \
una verificación de presencia literal del texto, no un juicio de plausibilidad.
"""

_VERIFY_JSON_SUFFIX = '\nReply ONLY with JSON: {"text_visible": <true|false>}'


def _strip_fences(content: str) -> str:
    content = content.strip()
    content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
    return re.sub(r"\s*```$", "", content).strip()


async def _structured_or_json(llm, prompt: str, img_b64: str, schema: type[BaseModel], json_suffix: str):
    """Ask for a structured field via with_structured_output; if that fails
    (some vision models don't support tool-calling reliably), retry once with
    an explicit "reply only with JSON" instruction and parse it by hand —
    same primary/fallback shape as triage.py."""
    image_block = {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}

    try:
        message = HumanMessage(content=[{"type": "text", "text": prompt}, image_block])
        return await llm.with_structured_output(schema).ainvoke([message])
    except Exception as exc:
        logger.warning("vision_structured_failed=%s falling back to json parse", exc)

    json_message = HumanMessage(content=[{"type": "text", "text": prompt + json_suffix}, image_block])
    resp = await llm.ainvoke([json_message])
    return schema.model_validate(json.loads(_strip_fences(resp.content)))


async def extract_procedure_query(img_bytes: bytes, caption: str) -> str:
    """Vision-transcribe a medical order/exam image into a literal price question.

    Shared by every channel (Telegram, WhatsApp, ...) that accepts image uploads,
    so the anti-hallucination prompt (literal transcription, explicit uncertainty
    signal) stays identical across channels instead of drifting.

    Two explicit verification layers, added after a live test showed the
    model confidently fabricating a procedure name on a blank image instead
    of flagging uncertainty:

    1. is_legible is a structured boolean field the model must set, not a
       sentinel string buried in free text it can choose to skip.
    2. Even with that field, the model still asserted is_legible=true on a
       blank image in roughly half of live runs — a self-report isn't
       trustworthy on its own. So a second, independent call re-examines the
       SAME image and is asked to specifically confirm or reject the first
       call's claimed text, rather than just re-stating confidence in it.

    Any failure at any stage — API error, malformed output, is_legible=false,
    or the second pass rejecting the claim — resolves to VISION_UNCERTAIN.
    The safe default here is "ask the user", never a guess.
    """
    if not settings.openai_vision_model:
        raise RuntimeError("OPENAI_VISION_MODEL not configured")

    llm = get_vision_llm()
    img_b64 = base64.b64encode(img_bytes).decode()
    prompt = f"{caption}\n\n{_VISION_EXTRACT_PROMPT}" if caption else _VISION_EXTRACT_PROMPT

    try:
        extraction: VisionExtraction = await _structured_or_json(
            llm, prompt, img_b64, VisionExtraction, _EXTRACT_JSON_SUFFIX
        )
    except Exception as exc:
        logger.warning("vision_extraction_failed=%s defaulting to uncertain", exc)
        return VISION_UNCERTAIN

    if not extraction.is_legible or not extraction.price_question:
        return VISION_UNCERTAIN

    verify_prompt = _VERIFY_PROMPT_TEMPLATE.format(claim=extraction.price_question)
    try:
        verification: VisionVerification = await _structured_or_json(
            llm, verify_prompt, img_b64, VisionVerification, _VERIFY_JSON_SUFFIX
        )
    except Exception as exc:
        logger.warning("vision_verification_failed=%s defaulting to uncertain", exc)
        return VISION_UNCERTAIN

    if not verification.text_visible:
        logger.warning("vision_verification_rejected claim=%s", extraction.price_question[:80])
        return VISION_UNCERTAIN

    return extraction.price_question
