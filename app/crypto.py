from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet | None:
    key = settings.fernet_key
    return Fernet(key.encode() if isinstance(key, str) else key) if key else None


def encrypt_value(value: str) -> str:
    f = _fernet()
    return f.encrypt(value.encode()).decode() if f else value


def decrypt_value(value: str) -> str:
    f = _fernet()
    return f.decrypt(value.encode()).decode() if f else value
