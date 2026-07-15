from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)

from app.models.base import Base


class IntegrationCredential(Base):
    """Generic per-tenant external-service credential store.

    Drive/Gmail today; Calendar/Calendly later reuse this table without a
    second migration — integration-specific behavior (scopes, refresh) lives
    in each service module, not the table (kept generic on purpose).

    encrypted_credentials is ALWAYS Fernet-encrypted (crypto.py). Unlike
    Tenant.wa_access_token (which tolerates a missing FERNET_KEY for backward
    compat), writes here are refused outright if FERNET_KEY is unset — see
    require_fernet() in app/crypto.py — since this table holds real OAuth
    tokens with external value, not internal channel secrets.
    """
    __tablename__ = "integration_credentials"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    integration_type = Column(String(32), nullable=False)  # "google_drive_gmail" today
    encrypted_credentials = Column(Text, nullable=False)
    credential_metadata = Column("metadata", JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "integration_type", name="uq_integration_credentials_tenant_type"),
    )
