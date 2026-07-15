"""Exact/structured-filter Drive search for the lab staff search feature
(phase 1 — see the CEO/eng review plan). The validated retrieval premise:
the lab's real files are named `{CODE}-{TYPE}-{PATIENT NAME}.pdf` (patient
name in the filename itself — confirmed empirically against a real file via
`name contains`, including multi-word phrase matching against a name that
sits mid-filename, not just as a prefix).

Only `patient_name` is confirmed present in the filename. `doctor`,
`test_type`, and `date` are NOT part of the observed naming convention —
ANDing a `name contains` clause for them against a filename that doesn't
carry that data would silently zero out every search. Those fields are
routed through `fullText contains` instead: Google's own server-side content
index (OCR/extracted text for PDFs), which may carry doctor/date/test-type
even when the filename doesn't — and critically, this is Google indexing
server-side, not the bot reading file bytes, so it doesn't reintroduce the
vision/content-read concern deferred to phase 2.

No free-form NLP, no LLM call in this path — filters are structured fields
supplied by staff (or a simple keyword split done by the caller).
"""
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from app.services.google_api_utils import (
    build_drive_client,
    refresh_if_needed,
    run_sync_with_retry,
    sanitize_filter_token,
)

logger = logging.getLogger(__name__)

_DRIVE_FIELDS = "files(id, name, modifiedTime, webViewLink, size)"

# Only patient_name is confirmed to live in the filename — everything else
# is searched against fullText (Google's content index) instead of name, so
# a doctor/date/test-type filter that isn't in the filename doesn't zero out
# the whole query. See module docstring.
_NAME_FIELDS = ("patient_name", "accession_id")
_FULLTEXT_FIELDS = ("doctor", "test_type", "date")


def build_query(filters: dict) -> str:
    """Build a Drive `q` string from sanitized filter tokens, ANDed together."""
    clauses = ["trashed = false", "mimeType != 'application/vnd.google-apps.folder'"]
    for key in _NAME_FIELDS:
        value = filters.get(key)
        if value:
            token = sanitize_filter_token(value)
            escaped = token.replace("\\", "\\\\").replace("'", "\\'")
            clauses.append(f"name contains '{escaped}'")
    for key in _FULLTEXT_FIELDS:
        value = filters.get(key)
        if value:
            token = sanitize_filter_token(value)
            escaped = token.replace("\\", "\\\\").replace("'", "\\'")
            clauses.append(f"fullText contains '{escaped}'")
    if len(clauses) <= 2:
        raise ValueError("At least one filter is required")
    return " and ".join(clauses)


async def search_files(
    creds: Credentials, filters: dict, limit: int = 5
) -> tuple[list[dict], bool]:
    """Returns (results capped at `limit`, has_more). Metadata only — never
    downloads or reads file content in this phase."""
    creds = await refresh_if_needed(creds)
    client = build_drive_client(creds)
    query = build_query(filters)

    try:
        response = await run_sync_with_retry(
            lambda: client.files().list(
                q=query, fields=_DRIVE_FIELDS, pageSize=limit + 1,
                orderBy="modifiedTime desc",
            ).execute()
        )
    except HttpError as exc:
        logger.warning("drive_search_failed status=%s", getattr(exc, "status_code", "?"))
        raise

    files = response.get("files", [])
    has_more = len(files) > limit
    return files[:limit], has_more


async def download_file_bytes(creds: Credentials, file_id: str) -> bytes:
    """Fetch raw file bytes for delivery to staff — a passthrough, not a
    content read/extraction (vision-based extraction is deferred to phase 2).
    Caller should check the file's `size` from search_files before calling,
    and use the file's webViewLink instead when it exceeds Telegram's limit."""
    creds = await refresh_if_needed(creds)
    client = build_drive_client(creds)
    request = client.files().get_media(fileId=file_id)
    return await run_sync_with_retry(request.execute)
