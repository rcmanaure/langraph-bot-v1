import hashlib
import hmac

from fastapi import Header, HTTPException
from sqlalchemy import text

from app.config import settings
from app.db import AsyncSessionLocal


def verify_operator_key(x_operator_key: str = Header(...)) -> None:
    # Prefer OPERATOR_TOKEN if set; fall back to SECRET_KEY so existing deploys aren't broken
    token = settings.operator_token or settings.secret_key
    if not hmac.compare_digest(x_operator_key, token):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def verify_tenant_scoped_key(x_operator_key: str = Header(...)) -> str:
    """Resolve the calling tenant from a per-tenant API key and return its slug.

    Reuses tenants.api_key_hash (already generated in admin.py's create_tenant
    and regen_key) instead of a new auth primitive — same sha256 hash, same
    `active = true` filter as the existing channel webhook lookups
    (whatsapp.py, telegram.py). Callers must additionally verify that any
    resource they act on (e.g. a thread_id) actually belongs to the returned
    tenant slug — this dependency only proves which tenant the key belongs to.
    """
    key_hash = hashlib.sha256(x_operator_key.encode()).hexdigest()
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            text("SELECT slug FROM tenants WHERE api_key_hash = :hash AND active = true"),
            {"hash": key_hash},
        )).first()
    if not row:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return row.slug
