import hashlib
import hmac

from fastapi import Header, HTTPException

from app.config import settings


def verify_operator_key(x_operator_key: str = Header(...)) -> None:
    # Prefer OPERATOR_TOKEN if set; fall back to SECRET_KEY so existing deploys aren't broken
    token = settings.operator_token or settings.secret_key
    expected = hashlib.sha256(token.encode()).hexdigest()
    if not hmac.compare_digest(x_operator_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
