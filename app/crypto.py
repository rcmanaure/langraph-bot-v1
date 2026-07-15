from cryptography.fernet import Fernet

from app.config import settings


class EncryptionNotConfiguredError(RuntimeError):
    """Raised by require_fernet() when FERNET_KEY is unset and the caller
    cannot tolerate encrypt_value's silent plaintext fallback (e.g. storing
    real OAuth tokens in integration_credentials — see app/models/
    integration_credential.py)."""


def require_fernet() -> None:
    if not settings.fernet_key:
        raise EncryptionNotConfiguredError(
            "FERNET_KEY must be configured before storing integration credentials"
        )


def _fernet() -> Fernet | None:
    key = settings.fernet_key
    return Fernet(key.encode() if isinstance(key, str) else key) if key else None


def encrypt_value(value: str) -> str:
    f = _fernet()
    return f.encrypt(value.encode()).decode() if f else value


def decrypt_value(value: str) -> str:
    f = _fernet()
    return f.decrypt(value.encode()).decode() if f else value
