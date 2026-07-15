"""Exact/structured-filter Gmail search for the lab staff search feature
(phase 1). Same scope discipline as drive.py: metadata/attachment-filename
matching only, no content extraction, no LLM call."""
import logging

from google.oauth2.credentials import Credentials
from googleapiclient.errors import HttpError

from app.services.google_api_utils import (
    build_gmail_client,
    refresh_if_needed,
    run_sync_with_retry,
    sanitize_filter_token,
)

logger = logging.getLogger(__name__)


def build_query(filters: dict) -> str:
    """Gmail search operators, not Drive's `contains` syntax — `has:attachment`
    restricts to messages carrying a file, since a lab result without an
    attachment is not a deliverable candidate in this feature."""
    clauses = ["has:attachment"]
    for key in ("patient_name", "doctor", "test_type", "date", "accession_id"):
        value = filters.get(key)
        if value:
            token = sanitize_filter_token(value)
            escaped = token.replace("\\", "\\\\").replace('"', '\\"')
            clauses.append(f'"{escaped}"')
    if len(clauses) <= 1:
        raise ValueError("At least one filter is required")
    return " ".join(clauses)


async def search_attachments(
    creds: Credentials, filters: dict, limit: int = 5
) -> tuple[list[dict], bool]:
    """Returns (results capped at `limit`, has_more). Each result: message id,
    subject, date, and attachment filename/id — metadata only."""
    creds = await refresh_if_needed(creds)
    client = build_gmail_client(creds)
    query = build_query(filters)

    try:
        response = await run_sync_with_retry(
            lambda: client.users().messages().list(
                userId="me", q=query, maxResults=limit + 1
            ).execute()
        )
    except HttpError as exc:
        logger.warning("gmail_search_failed status=%s", getattr(exc, "status_code", "?"))
        raise

    message_refs = response.get("messages", [])
    has_more = len(message_refs) > limit
    results = []
    for ref in message_refs[:limit]:
        msg = await run_sync_with_retry(
            lambda ref=ref: client.users().messages().get(
                userId="me", id=ref["id"], format="metadata",
                metadataHeaders=["Subject", "Date"],
            ).execute()
        )
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        attachments = [
            {"filename": part["filename"], "attachment_id": part["body"]["attachmentId"]}
            for part in msg.get("payload", {}).get("parts", [])
            if part.get("filename") and part.get("body", {}).get("attachmentId")
        ]
        results.append({
            "message_id": msg["id"],
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "attachments": attachments,
        })
    return results, has_more


async def download_attachment_bytes(creds: Credentials, message_id: str, attachment_id: str) -> bytes:
    """Fetch raw attachment bytes for delivery — passthrough, not extraction."""
    import base64

    creds = await refresh_if_needed(creds)
    client = build_gmail_client(creds)
    attachment = await run_sync_with_retry(
        lambda: client.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()
    )
    return base64.urlsafe_b64decode(attachment["data"])
