"""Google OAuth (Gmail.readonly + Drive.readonly) for the patient-results
search feature (D2, D8). The connect link is generated from within the
already-authenticated admin panel; Google's redirect can't carry our custom
X-Operator-Key header, so the callback proves identity via a signed, short-lived,
single-use `state` token instead (D8) — same trust model as `webhook_secret`,
just JWT-shaped (pyjwt is already a dependency) instead of hand-rolled HMAC.
"""
import time
import uuid
from collections import OrderedDict

import jwt
from google.auth.exceptions import RefreshError as _GoogleRefreshError
from google.auth.exceptions import TransportError as _GoogleTransportError
from google.auth.transport.requests import Request as _GoogleAuthRequest
from google.oauth2 import id_token as _google_id_token
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

from app.channels.base import dedup_seen
from app.config import settings
from app.services.llm import call_with_retry_sync

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
]

_STATE_TTL_SECONDS = 300  # 5 min — a connect link is a one-time action, not a bookmark
_SEEN_STATE_NONCES: "OrderedDict[str, bool]" = OrderedDict()
_SEEN_STATE_MAX = 1000


class OAuthError(Exception):
    """Invalid/expired authorization code, or invalid/expired/reused state."""


class RefreshError(Exception):
    """Refresh token was revoked — tenant needs to reconnect."""


def build_state(tenant_slug: str) -> str:
    payload = {
        "tenant_slug": tenant_slug,
        "nonce": uuid.uuid4().hex,
        "exp": int(time.time()) + _STATE_TTL_SECONDS,
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def verify_state(state: str) -> str:
    """Validate and consume a state token. Returns tenant_slug.

    Raises OAuthError on invalid, expired, or already-used state. Single-use
    is enforced with the same LRU-bounded seen-set the channel adapters use
    for webhook dedup (app.channels.base.dedup_seen) — same shape of problem
    (has this key been consumed before?), no new mechanism needed.
    """
    try:
        payload = jwt.decode(state, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise OAuthError(f"invalid state: {exc}") from exc

    nonce = payload.get("nonce", "")
    if dedup_seen(_SEEN_STATE_NONCES, nonce, _SEEN_STATE_MAX):
        raise OAuthError("state already used")

    return payload["tenant_slug"]


def _client_config() -> dict:
    return {
        "web": {
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    }


def get_authorization_url(tenant_slug: str, redirect_uri: str) -> str:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    state = build_state(tenant_slug)
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
        include_granted_scopes="true",
    )
    return auth_url


def exchange_code(code: str, redirect_uri: str) -> Credentials:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=redirect_uri)
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise OAuthError(str(exc)) from exc
    return flow.credentials


def get_verified_email(credentials: Credentials) -> str | None:
    """Best-effort: the connected Gmail/Drive account's email, for admin
    display only (not used for auth). Verified via Google's own certs so we
    don't trust an unverified id_token claim."""
    if not credentials.id_token:
        return None
    try:
        claims = _google_id_token.verify_oauth2_token(
            credentials.id_token, _GoogleAuthRequest(), settings.google_client_id
        )
    except Exception:
        return None
    return claims.get("email")


def _is_transient_network_error(exc: BaseException) -> bool:
    # A revoked refresh token also surfaces as _GoogleRefreshError in some
    # google-auth versions, but that's handled separately below (not
    # transient — retrying won't un-revoke it).
    return isinstance(exc, (_GoogleTransportError, TimeoutError, ConnectionError))


def refresh_access_token(refresh_token: str) -> Credentials:
    creds = Credentials(
        None,
        refresh_token=refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )
    try:
        call_with_retry_sync(creds.refresh, _GoogleAuthRequest(), is_retryable=_is_transient_network_error)
    except _GoogleRefreshError as exc:
        raise RefreshError(str(exc)) from exc
    return creds
