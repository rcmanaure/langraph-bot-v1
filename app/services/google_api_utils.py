"""Shared plumbing for Drive/Gmail: OAuth credential (de)serialization, the
sync-client-on-async-loop executor wrap, retry/backoff, and query sanitization.

Centralized here (rather than duplicated in drive.py/gmail.py) because both
services need identical treatment of a security-relevant concern: sanitizing
staff-supplied filter text before it becomes part of a Drive/Gmail query
string. Two copies of that logic drifting apart is exactly the kind of bug
a single shared module prevents.
"""
import asyncio
import functools
import json
import logging
import re

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.crypto import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

# Read-only by design — this feature searches and fetches, never writes or
# deletes. Least-privilege scoping of WHICH files/folders/labels the shared
# service account can see is a Google Workspace admin-console task (see the
# plan's "What Already Exists" / TODOS — not something these scopes control).
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/gmail.readonly",
]

_RETRYABLE_STATUS = {429, 500, 502, 503}
_MAX_RETRIES = 2
_BASE_DELAY = 1.0  # seconds


class DriveAuthError(Exception):
    """OAuth token invalid/revoked/refresh failed — feature-wide outage until re-auth."""


class DriveTimeoutError(Exception):
    """API call timed out."""


class DriveRateLimitError(Exception):
    """429 after exhausting retries."""


class DriveQueryError(Exception):
    """Filter text failed sanitization — never built into a raw query string."""


def credentials_to_json(creds: Credentials) -> str:
    """Serialize google.oauth2.credentials.Credentials to a JSON string for
    encrypted storage in integration_credentials.encrypted_credentials."""
    return creds.to_json()


def credentials_from_json(raw_json: str) -> Credentials:
    return Credentials.from_authorized_user_info(json.loads(raw_json), scopes=GOOGLE_SCOPES)


def encrypt_credentials(creds: Credentials) -> str:
    return encrypt_value(credentials_to_json(creds))


def decrypt_credentials(encrypted: str) -> Credentials:
    return credentials_from_json(decrypt_value(encrypted))


async def refresh_if_needed(creds: Credentials) -> Credentials:
    """Refresh an expired token. Sync google-auth call — wrapped in an
    executor like every other Google API call here (see run_sync)."""
    if creds.valid:
        return creds
    if not creds.refresh_token:
        raise DriveAuthError("No refresh token available — tenant must reconnect Google")
    try:
        await run_sync(creds.refresh, GoogleAuthRequest())
    except RefreshError as exc:
        raise DriveAuthError(f"Google refresh token rejected: {exc}") from exc
    return creds


async def run_sync(fn, *args, **kwargs):
    """Run a synchronous call (google-api-python-client is httplib2-based,
    no native asyncio support) in a threadpool executor. Required because
    this app runs a single async worker process (entrypoint.sh --workers 1)
    — calling a blocking Google API request directly on the event loop would
    stall every other tenant's webhook for the duration of the call."""
    loop = asyncio.get_running_loop()
    call = functools.partial(fn, *args, **kwargs)
    return await loop.run_in_executor(None, call)


async def run_sync_with_retry(fn, *args, **kwargs):
    """run_sync + retry/backoff on transient errors (429/5xx), fail-fast on
    everything else. Auth failures are never retried — retrying a rejected
    credential just wastes 3 round-trips before reporting the same failure."""
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await run_sync(fn, *args, **kwargs)
        except HttpError as exc:
            status = getattr(exc, "status_code", None) or exc.resp.status
            if status == 429 and attempt < _MAX_RETRIES:
                delay = _BASE_DELAY * (2**attempt)
                logger.warning("google_api_rate_limited attempt=%d retrying_in=%.1fs", attempt + 1, delay)
                await asyncio.sleep(delay)
                continue
            if status == 429:
                raise DriveRateLimitError(str(exc)) from exc
            if status in _RETRYABLE_STATUS and attempt < _MAX_RETRIES:
                delay = _BASE_DELAY * (2**attempt)
                await asyncio.sleep(delay)
                continue
            if status in (401, 403):
                raise DriveAuthError(str(exc)) from exc
            raise
        except TimeoutError as exc:
            raise DriveTimeoutError(str(exc)) from exc


def build_drive_client(creds: Credentials):
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def build_gmail_client(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# Drive/Gmail query syntax gives special meaning to quotes and backslashes
# (e.g. `name contains 'value'`) — a staff-supplied filter containing an
# unescaped quote could break out of the intended field and inject extra
# query clauses. Control characters have no legitimate use in a patient/
# doctor/test-type filter and are rejected outright. Quotes and backslashes
# ARE legitimate (e.g. "O'Brien") — those are escaped by each call site
# (drive.py/gmail.py) instead of rejected here, since Drive's single-quoted
# and Gmail's double-quoted query syntax need different escaping.
_UNSAFE_QUERY_CHARS = re.compile(r"[\x00-\x1f]")
MAX_FILTER_LEN = 200


def sanitize_filter_token(value: str) -> str:
    """Validate + length-cap a staff-supplied filter value. Raises
    DriveQueryError on control characters; quote/backslash escaping for
    query interpolation is the caller's responsibility (see module docstring)."""
    if not value or not value.strip():
        raise DriveQueryError("Filtro vacío")
    value = value.strip()[:MAX_FILTER_LEN]
    if _UNSAFE_QUERY_CHARS.search(value):
        raise DriveQueryError("Filtro contiene caracteres no permitidos")
    return value
