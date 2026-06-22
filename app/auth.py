import hashlib
import hmac

from fastapi import Header, HTTPException

from app.config import settings


def verify_operator_key(x_operator_key: str = Header(...)) -> None:
    # ponytail: uses SECRET_KEY — T8 replaces with dedicated operator tokens + bcrypt
    expected = hashlib.sha256(settings.secret_key.encode()).hexdigest()
    if not hmac.compare_digest(x_operator_key, expected):
        raise HTTPException(status_code=401, detail="Unauthorized")
