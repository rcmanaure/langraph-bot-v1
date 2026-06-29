import hashlib
import logging
import re
import secrets
import unicodedata

logger = logging.getLogger(__name__)

# Rotates at startup — unpredictable to attackers.
# ponytail: single-worker safe; multi-worker needs Redis for shared canary
CANARY_TOKEN = secrets.token_hex(8)

_INJECTION_RE = re.compile(
    r"(ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?|rules?)"
    r"|system\s*prompt"
    r"|you\s+are\s+now\s+\w+"
    r"|act\s+as\s+(if\s+you\s+are|a\s)"
    r"|jailbreak"
    r"|do\s+anything\s+now"
    r"|roleplay\s+as"
    r"|pretend\s+(you\s+are|to\s+be)"
    r"|forget\s+(everything|all)\s+(you|your)"
    r"|override\s+(your\s+)?(instructions?|rules?)"
    r"|from\s+now\s+on\s+(you|ignore)"
    r"|reveal\s+(your\s+)?(system\s+)?prompt"
    r"|print\s+(your\s+)?(system\s+)?prompt"
    # Spanish variants
    r"|ignora\s+(tus?\s+|todas?\s+las?\s+)?(instrucciones|reglas|prompt)"
    r"|olvida\s+(todas?\s+las?\s+)?(instrucciones|reglas|todo\s+lo\s+anterior)"
    r"|act[uú]a\s+como\s+si"
    r"|desde\s+ahora\s+ignora"
    r"|revelar?\s+(tu\s+)?(prompt|instrucciones|sistema)"
    r"|mostrar?\s+(tu\s+)?(prompt|instrucciones)"
    r"|sin\s+restricciones"
    r"|CANARY_KEY:)",
    re.IGNORECASE | re.DOTALL,
)

_THREAD_RE = re.compile(
    r"^tenant:[a-z0-9-]+:user:[0-9]+:channel:(telegram|whatsapp)(:v[0-9]+)?$"
)

MAX_INPUT_CHARS = 2000


def sanitize_user_input(text: str) -> str:
    """Normalize + scan for injection. Raises ValueError if detected. Truncates at MAX_INPUT_CHARS."""
    if not text:
        return text
    text = unicodedata.normalize("NFC", text)
    if _INJECTION_RE.search(text):
        logger.warning(
            "prompt_injection_attempt hash=%s snippet=%r",
            hashlib.sha256(text.encode()).hexdigest()[:8],
            text[:50],
        )
        raise ValueError("Mensaje no permitido.")
    return text[:MAX_INPUT_CHARS]


def scan_chunk_for_injection(content: str) -> bool:
    """True if chunk content looks like an injection attempt — skip during indexing."""
    return bool(_INJECTION_RE.search(content))


def validate_output_canary(response: str, user_id: str = "") -> str:
    """Log canary exfiltration if detected. Returns response unchanged."""
    if CANARY_TOKEN in response:
        logger.warning(
            "canary_exfiltration_detected uid=%s snippet=%r",
            user_id,
            response[:100],
        )
    return response


def validate_thread_id(thread_id: str) -> bool:
    return bool(_THREAD_RE.match(thread_id))
