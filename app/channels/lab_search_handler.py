"""Staff-facing lab result search (phase 1). A plain function, not a
StateGraph node — a single-turn tool call doesn't fit the
triage→retrieve→generate conversational pipeline built for RAG.

No vision, no LLM call, no result cache in this phase — see the CEO/eng
review plan (~/.gstack/projects/rcmanaure-langraph-bot-v1/ceo-plans/
2026-07-15-lab-staff-search.md) for why. Session/pending-confirm state is
in-process (dict), matching the same safety assumption as telegram.py's
_MEDIA_GROUPS/_SEEN_UPDATES: this app runs a single async worker
(entrypoint.sh --workers 1), so in-process state never needs cross-worker
coordination.

Callers must insert `handle_staff_message` ABOVE both the existing
photo→vision routing and the normal triage/StateGraph path in telegram.py —
staff messages must never reach either.
"""
import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy import select, text

from app.db import AsyncSessionLocal
from app.models import IntegrationCredential, SearchAudit, StaffSecret
from app.services.drive import search_files as drive_search_files
from app.services.gmail import search_attachments as gmail_search_attachments
from app.services.google_api_utils import (
    DriveAuthError,
    DriveQueryError,
    DriveRateLimitError,
    DriveTimeoutError,
    decrypt_credentials,
)

logger = logging.getLogger(__name__)

SendFn = Callable[[str, str], Awaitable[None]]

_SESSION_TTL_SECONDS = 30 * 60
_CONFIRM_TIMEOUT_SECONDS = 5 * 60
_RESULT_LIMIT = 5


@dataclass
class _StaffSession:
    secret_id: int
    label: str
    expires_at: float


@dataclass
class _PendingConfirm:
    candidates: list[dict]
    source: str  # "drive" | "gmail"
    filters: dict
    expires_at: float
    delivered: bool = False


# Keyed on (tenant_slug, user_id) — NEVER chat_id. Telegram group chats: chat_id
# is the GROUP, user_id is the sender; caching on chat_id would leak staff
# access to everyone in a shared group (see the plan's Correction #2).
_SESSIONS: dict[tuple[str, str], _StaffSession] = {}
_PENDING: dict[tuple[str, str], _PendingConfirm] = {}


def _session_key(tenant_slug: str, user_id: str) -> tuple[str, str]:
    return (tenant_slug, user_id)


def _get_session(tenant_slug: str, user_id: str) -> _StaffSession | None:
    """Lazy check-on-read TTL — no scheduled cleanup job needed at this
    volume (matches T-cache's TTL strategy for search_result_cache in
    phase 2's design, applied here for session expiry too)."""
    key = _session_key(tenant_slug, user_id)
    session = _SESSIONS.get(key)
    if session is None:
        return None
    if session.expires_at < time.monotonic():
        _SESSIONS.pop(key, None)
        _PENDING.pop(key, None)
        return None
    return session


def _start_session(tenant_slug: str, user_id: str, secret_id: int, label: str) -> None:
    _SESSIONS[_session_key(tenant_slug, user_id)] = _StaffSession(
        secret_id=secret_id, label=label, expires_at=time.monotonic() + _SESSION_TTL_SECONDS
    )


def _get_pending(tenant_slug: str, user_id: str) -> _PendingConfirm | None:
    key = _session_key(tenant_slug, user_id)
    pending = _PENDING.get(key)
    if pending is None:
        return None
    if pending.expires_at < time.monotonic():
        _PENDING.pop(key, None)
        return None
    return pending


# ── Unlock ───────────────────────────────────────────────────────────────────

@dataclass
class _UnlockOutcome:
    result: str  # "no_match" | "rejected" | "bound"
    label: str | None = None
    secret_id: int | None = None


async def _try_unlock(tenant_id: int, tenant_slug: str, user_id: str, message_text: str) -> _UnlockOutcome:
    candidate = message_text.strip()
    if len(candidate) < 16:  # real secrets are 48 hex chars — cheap short-circuit
        return _UnlockOutcome("no_match")

    secret_hash = hashlib.sha256(candidate.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        secret = (await db.execute(
            select(StaffSecret).where(
                StaffSecret.tenant_id == tenant_id,
                StaffSecret.secret_hash == secret_hash,
                StaffSecret.revoked_at.is_(None),
            )
        )).scalar_one_or_none()

        if not secret:
            return _UnlockOutcome("no_match")

        if secret.bound_user_id is not None and secret.bound_user_id != user_id:
            # A real, valid secret — just not for this user_id. Reject
            # without revealing it's "already claimed" (see plan's Failure
            # Modes Registry: generic error, no hint it belongs to someone else).
            logger.warning(
                "staff_secret_rebind_rejected tenant=%s secret_id=%s attempted_user=%s",
                tenant_slug, secret.id, user_id,
            )
            return _UnlockOutcome("rejected")

        if secret.bound_user_id is None:
            await db.execute(
                text("UPDATE staff_secrets SET bound_user_id = :uid WHERE id = :id"),
                {"uid": user_id, "id": secret.id},
            )
            await db.commit()

        return _UnlockOutcome("bound", secret.label, secret.id)


# ── Filter parsing (structured, no LLM) ─────────────────────────────────────

_FIELD_ALIASES = {
    "nombre": "patient_name", "paciente": "patient_name", "name": "patient_name",
    "doctor": "doctor", "medico": "doctor", "médico": "doctor",
    "tipo": "test_type", "examen": "test_type", "estudio": "test_type", "prueba": "test_type",
    "fecha": "date", "date": "date",
    "codigo": "accession_id", "código": "accession_id", "accession": "accession_id", "id": "accession_id",
}
_SPLIT_RE = re.compile(r"[;\n]+|,(?=\s*\w+\s*:)")


def parse_filters(message_text: str) -> dict:
    """Structured `key: value` pairs (semicolon/newline separated), or plain
    text treated as a patient-name-only search. Deliberately no NLP/LLM —
    see the plan's Round 2 finding on why free-form parsing is deferred."""
    message_text = message_text.strip()
    filters: dict[str, str] = {}
    if ":" in message_text:
        for part in _SPLIT_RE.split(message_text):
            if ":" not in part:
                continue
            key, _, value = part.partition(":")
            field_name = _FIELD_ALIASES.get(key.strip().lower())
            value = value.strip()
            if field_name and value:
                filters[field_name] = value
    if not filters and message_text:
        filters["patient_name"] = message_text
    return filters


# ── Search ───────────────────────────────────────────────────────────────────

async def _get_google_credentials(tenant_id: int):
    async with AsyncSessionLocal() as db:
        cred = (await db.execute(
            select(IntegrationCredential).where(
                IntegrationCredential.tenant_id == tenant_id,
                IntegrationCredential.integration_type == "google_drive_gmail",
            )
        )).scalar_one_or_none()
    if not cred:
        return None
    return decrypt_credentials(cred.encrypted_credentials)


async def _write_audit(tenant_id: int, label: str, filters: dict, result_count: int, delivered: bool) -> None:
    """Best-effort — a write failure must never block the response (this is
    an internal tool, not the patient-facing fail-closed gate)."""
    try:
        async with AsyncSessionLocal() as db:
            db.add(SearchAudit(
                tenant_id=tenant_id,
                staff_secret_label=label,
                filters_used=str(filters),
                result_count=result_count,
                delivered=delivered,
            ))
            await db.commit()
    except Exception as exc:
        logger.warning("search_audit_write_failed tenant_id=%s err=%s", tenant_id, exc)


def _format_candidate(idx: int, source: str, item: dict) -> str:
    if source == "drive":
        return f"{idx}. {item['name']} ({item.get('modifiedTime', '?')[:10]})"
    subject = item.get("subject") or "(sin asunto)"
    names = ", ".join(a["filename"] for a in item.get("attachments", []))
    return f"{idx}. {subject} — {names} ({item.get('date', '?')})"


async def _handle_search(
    tenant_id: int, tenant_slug: str, user_id: str, label: str, message_text: str, send: SendFn, chat_id: str,
) -> None:
    filters = parse_filters(message_text)
    creds = await _get_google_credentials(tenant_id)
    if not creds:
        await send(chat_id, "La conexión con Google no está configurada para este tenant. Contactá soporte.")
        return

    try:
        drive_results, drive_more = await drive_search_files(creds, filters, limit=_RESULT_LIMIT)
    except DriveQueryError as exc:
        await send(chat_id, str(exc))
        return
    except DriveAuthError:
        logger.error("drive_auth_error tenant=%s", tenant_slug)
        await send(chat_id, "Búsqueda no disponible, contactá soporte.")
        return
    except (DriveTimeoutError, DriveRateLimitError):
        await send(chat_id, "No pude buscar ahora, probá de nuevo.")
        return
    except Exception:
        logger.exception("drive_search_unexpected_error tenant=%s", tenant_slug)
        await send(chat_id, "No pude buscar ahora, probá de nuevo.")
        return

    candidates = drive_results
    source = "drive"
    has_more = drive_more
    narrowed_to_name_only = False

    # doctor/test_type/date go through fullText (Google's content index —
    # see drive.py), which is best-effort: it only helps if Google actually
    # OCR-indexed the PDF body. If a multi-filter search comes back empty,
    # retry with just the name-based filters (name contains — verified to
    # work) before giving up or falling back to Gmail.
    name_only_filters = {k: v for k, v in filters.items() if k in ("patient_name", "accession_id")}
    if not candidates and name_only_filters and name_only_filters != filters:
        try:
            retry_results, retry_more = await drive_search_files(creds, name_only_filters, limit=_RESULT_LIMIT)
        except Exception:
            retry_results, retry_more = [], False
        if retry_results:
            candidates, has_more, narrowed_to_name_only = retry_results, retry_more, True

    if not candidates:
        try:
            gmail_results, gmail_more = await gmail_search_attachments(creds, filters, limit=_RESULT_LIMIT)
            candidates, source, has_more = gmail_results, "gmail", gmail_more
        except DriveQueryError as exc:
            await send(chat_id, str(exc))
            return
        except DriveAuthError:
            logger.error("gmail_auth_error tenant=%s", tenant_slug)
            await send(chat_id, "Búsqueda no disponible, contactá soporte.")
            return
        except (DriveTimeoutError, DriveRateLimitError):
            await send(chat_id, "No pude buscar ahora, probá de nuevo.")
            return
        except Exception:
            logger.exception("gmail_search_unexpected_error tenant=%s", tenant_slug)
            await send(chat_id, "No pude buscar ahora, probá de nuevo.")
            return

    await _write_audit(tenant_id, label, filters, len(candidates), delivered=False)

    if not candidates:
        await send(chat_id, "No encontré nada con esos filtros, probá con otro dato.")
        return

    if has_more:
        await send(chat_id, "Encontré más de 5 resultados. Agregá otro filtro para acotar la búsqueda.")
        return

    key = _session_key(tenant_slug, user_id)
    pending = _PendingConfirm(
        candidates=candidates, source=source, filters=filters,
        expires_at=time.monotonic() + _CONFIRM_TIMEOUT_SECONDS,
    )
    _PENDING[key] = pending
    asyncio.create_task(_expire_pending_after_timeout(key, pending, chat_id, send))

    prefix = "No encontré nada con esos filtros exactos, pero esto coincide solo por nombre:\n" \
        if narrowed_to_name_only else ""
    lines = [_format_candidate(i + 1, source, c) for i, c in enumerate(candidates)]
    if len(candidates) == 1:
        await send(chat_id, f"{prefix}Encontré:\n{lines[0]}\n\n¿Confirmás el envío? (sí/no)")
    else:
        await send(chat_id, f"{prefix}Encontré varios resultados:\n" + "\n".join(lines)
                   + "\n\nRespondé con el número para confirmar, o 'no' para cancelar.")


async def _expire_pending_after_timeout(
    key: tuple[str, str], pending: _PendingConfirm, chat_id: str, send: SendFn
) -> None:
    """Proactively notify on timeout (matches telegram.py's own
    _process_media_group debounce-task pattern) rather than only relying on
    the lazy check-on-read in _get_pending to silently drop stale state."""
    await asyncio.sleep(_CONFIRM_TIMEOUT_SECONDS + 1)
    if _PENDING.get(key) is pending:  # still the same, un-actioned pending
        _PENDING.pop(key, None)
        await send(chat_id, "Búsqueda cancelada por inactividad.")


_YES_RE = re.compile(r"^\s*(s[ií]|1|confirmar?)\s*$", re.IGNORECASE)
_NO_RE = re.compile(r"^\s*(no|cancelar?)\s*$", re.IGNORECASE)


async def _handle_confirm_reply(
    tenant_id: int, tenant_slug: str, user_id: str, chat_id: str,
    message_text: str, pending: _PendingConfirm, label: str, send: SendFn,
) -> None:
    key = _session_key(tenant_slug, user_id)
    reply = message_text.strip()

    if _NO_RE.match(reply):
        _PENDING.pop(key, None)
        await send(chat_id, "Búsqueda cancelada.")
        return

    selected: dict | None = None
    if len(pending.candidates) == 1 and _YES_RE.match(reply):
        selected = pending.candidates[0]
    elif len(pending.candidates) > 1 and reply.isdigit():
        idx = int(reply) - 1
        if 0 <= idx < len(pending.candidates):
            selected = pending.candidates[idx]

    if selected is None:
        await send(chat_id, "No entendí. Respondé con el número para confirmar, o 'no' para cancelar.")
        return

    _PENDING.pop(key, None)
    await _deliver(tenant_id, label, pending.source, selected, pending.filters, chat_id, send)


async def _deliver(
    tenant_id: int, label: str, source: str, item: dict, filters: dict, chat_id: str, send: SendFn,
) -> None:
    """Confirmed delivery. Sends the Drive link — actual file bytes/size-cap
    handling (Telegram document upload for small files, link for oversized
    ones) lives in the channel-specific send path; kept to a link here to
    stay channel-agnostic in this module (see NOT in Scope for anything more)."""
    if source == "drive":
        link = item.get("webViewLink") or f"https://drive.google.com/file/d/{item['id']}/view"
        await send(chat_id, f"Acá está: {link}")
    else:
        await send(chat_id, f"Encontrado en Gmail: \"{item.get('subject', '')}\" ({item.get('date', '')})")
    await _write_audit(tenant_id, label, filters, 1, delivered=True)


def is_staff_session_active(tenant_slug: str, user_id: str) -> bool:
    """Used by telegram.py to catch non-text media (photo/audio) from an
    already-unlocked staff session — image-as-trigger is phase 2, not built
    here, so those must not fall through to the patient vision pipeline."""
    return _get_session(tenant_slug, user_id) is not None


# ── Entry point ──────────────────────────────────────────────────────────────

async def handle_staff_message(
    tenant_id: int, tenant_slug: str, user_id: str, chat_id: str, message_text: str, send: SendFn,
) -> bool:
    """Returns True if the message was handled as staff-mode (caller must
    NOT continue to the existing photo/triage routing), False otherwise."""
    session = _get_session(tenant_slug, user_id)

    if session is None:
        outcome = await _try_unlock(tenant_id, tenant_slug, user_id, message_text)
        if outcome.result == "no_match":
            return False
        if outcome.result == "rejected":
            await send(chat_id, "Clave incorrecta.")
            return True
        _start_session(tenant_slug, user_id, outcome.secret_id, outcome.label)
        await send(chat_id, f"Listo, {outcome.label}. ¿Qué buscás?")
        return True

    pending = _get_pending(tenant_slug, user_id)
    if pending is not None:
        await _handle_confirm_reply(
            tenant_id, tenant_slug, user_id, chat_id, message_text, pending, session.label, send
        )
        return True

    await _handle_search(tenant_id, tenant_slug, user_id, session.label, message_text, send, chat_id)
    return True
