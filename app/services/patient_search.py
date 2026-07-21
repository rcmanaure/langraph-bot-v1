"""Patient search: disambiguate a person against patient_index (D4/D5), then
search Gmail/Drive by name. patient_index only proves WHICH PERSON we mean —
it can't filter Google's own free-text search, so each result is classified
as "confirmed" (the DNI/DOB is found in the result's own text) or
"unconfirmed" (name matched, but no verifiable identifier — eng-review
2026-07-20, outside-voice finding 1: two patients can share a full name).
"""
import time

import jwt
from googleapiclient.discovery import build as google_build
from googleapiclient.errors import HttpError as GoogleHttpError

from app.config import settings
from app.services.google_oauth import RefreshError, refresh_access_token
from app.services.llm import call_with_retry_sync

_RESULT_ID_TTL_SECONDS = 900  # 15 min — long enough for the admin to click download


def _is_google_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, GoogleHttpError) and getattr(exc.resp, "status", None) == 429


class SearchValidationError(Exception):
    """Bad input — 422."""


class PatientNotFoundError(Exception):
    """No patient_index row matches name+dni_or_dob — not an error, empty result."""


class AmbiguousPatientError(Exception):
    """Multiple patient_index rows matched name+dni_or_dob (duplicate entries —
    a data-quality issue, not the common case since dni_or_dob is required).
    Caller must present the list, never auto-pick."""

    def __init__(self, candidates: list[dict]):
        self.candidates = candidates


def validate_query(name: str, dni_or_dob: str) -> None:
    if not name or not name.strip():
        raise SearchValidationError("name is required")
    if not dni_or_dob or not dni_or_dob.strip():
        raise SearchValidationError("DNI o fecha de nacimiento requerida para desambiguar")


async def disambiguate_patient(db, tenant_id: int, name: str, dni_or_dob: str) -> dict:
    """Returns the single matching patient_index row. Raises PatientNotFoundError
    or AmbiguousPatientError otherwise."""
    from sqlalchemy import text

    rows = (await db.execute(
        text("""
            SELECT id, patient_name, dni_or_dob
              FROM patient_index
             WHERE tenant_id = :tenant_id
               AND lower(patient_name) = lower(:name)
               AND dni_or_dob = :dni_or_dob
        """),
        {"tenant_id": tenant_id, "name": name.strip(), "dni_or_dob": dni_or_dob.strip()},
    )).fetchall()

    if not rows:
        raise PatientNotFoundError()
    candidates = [dict(r._mapping) for r in rows]
    if len(candidates) > 1:
        raise AmbiguousPatientError(candidates)
    return candidates[0]


def escape_gmail_query_term(term: str) -> str:
    """Quote as a literal phrase so admin-controlled text (a patient_index
    name) can never be interpreted as a Gmail search operator (from:,
    has:attachment, etc — Section 3 threat model)."""
    return f'"{term.replace(chr(34), "")}"'


def is_confirmed(verify_text: str, dni_or_dob: str) -> bool:
    """A result counts as "match exacto" only if the verifiable identifier
    appears in its own text — matching by name alone is not enough."""
    if not verify_text or not dni_or_dob:
        return False
    return dni_or_dob.strip() in verify_text


def search_gmail(service, query: str) -> list[dict]:
    """Thin wrapper around the Gmail API — separate from patient_search() so
    tests can mock `service` without a real Google connection. Retries once
    on 429 (Section 2 error map); a malformed response is a KeyError/ValueError
    caught explicitly here rather than a bare except (Section 2 GAP — same
    pattern as _structured_or_json/rerank_chunks elsewhere in this repo)."""
    list_resp = call_with_retry_sync(
        service.users().messages().list(userId="me", q=query, maxResults=20).execute,
        is_retryable=_is_google_rate_limited,
    )
    results = []
    try:
        for msg_ref in list_resp.get("messages", []):
            msg = call_with_retry_sync(
                service.users().messages().get(
                    userId="me", id=msg_ref["id"], format="metadata",
                    metadataHeaders=["Subject"],
                ).execute,
                is_retryable=_is_google_rate_limited,
            )
            subject = next(
                (h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"] == "Subject"),
                "",
            )
            snippet = msg.get("snippet", "")
            for part in msg.get("payload", {}).get("parts") or []:
                filename = part.get("filename", "")
                attachment_id = part.get("body", {}).get("attachmentId")
                if not filename or not attachment_id:
                    continue
                results.append({
                    "source": "gmail",
                    "message_id": msg_ref["id"],
                    "attachment_id": attachment_id,
                    "filename": filename,
                    "verify_text": f"{subject} {snippet} {filename}",
                })
    except (KeyError, ValueError) as exc:
        raise SearchValidationError(f"malformed Gmail response: {exc}") from exc
    return results


def search_drive(service, query: str) -> list[dict]:
    resp = call_with_retry_sync(
        service.files().list(
            q=f"fullText contains {query} and trashed = false",
            fields="files(id, name)",
            pageSize=20,
        ).execute,
        is_retryable=_is_google_rate_limited,
    )
    try:
        return [
            {"source": "drive", "file_id": f["id"], "filename": f["name"], "verify_text": f["name"]}
            for f in resp.get("files", [])
        ]
    except (KeyError, ValueError) as exc:
        raise SearchValidationError(f"malformed Drive response: {exc}") from exc


def build_result_id(tenant_id: int, source: str, **ids) -> str:
    """Signed, source-specific envelope (eng-review 2026-07-20, outside-voice
    finding 7): Drive needs only file_id; Gmail needs message_id AND
    attachment_id (message + part are 2 distinct IDs). Signed so a client
    can't forge a result_id for another tenant's file."""
    payload = {"tenant_id": tenant_id, "source": source, "exp": int(time.time()) + _RESULT_ID_TTL_SECONDS, **ids}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_result_id(token: str) -> dict:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise SearchValidationError(f"invalid result_id: {exc}") from exc


async def patient_search(db, tenant, name: str, dni_or_dob: str) -> dict:
    validate_query(name, dni_or_dob)
    patient = await disambiguate_patient(db, tenant.id, name, dni_or_dob)

    if not tenant.google_refresh_token:
        raise RefreshError("tenant has no connected Google account")
    creds = refresh_access_token(tenant.google_refresh_token)

    query = escape_gmail_query_term(patient["patient_name"])
    gmail_results = search_gmail(google_build("gmail", "v1", credentials=creds), query)
    drive_results = search_drive(google_build("drive", "v3", credentials=creds), query)

    results = []
    for r in gmail_results + drive_results:
        confirmed = is_confirmed(r["verify_text"], dni_or_dob)
        if r["source"] == "gmail":
            result_id = build_result_id(
                tenant.id, "gmail", message_id=r["message_id"], attachment_id=r["attachment_id"]
            )
        else:
            result_id = build_result_id(tenant.id, "drive", file_id=r["file_id"])
        results.append({
            "source": r["source"],
            "filename": r["filename"],
            "result_id": result_id,
            "status": "match_exacto" if confirmed else "candidato_no_confirmado",
        })

    return {"patient_name": patient["patient_name"], "results": results}
