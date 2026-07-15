from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint, func

from app.models.base import Base


class StaffSecret(Base):
    """Named, admin-issued unlock secret for one lab employee.

    Hashed like Tenant.api_key_hash — equality-check only, never read back.
    Bound to the first Telegram user_id that redeems it, so search_audit can
    attribute a search to a real named person instead of "someone who knew
    the secret" (a single shared secret can't do that — see the lab-staff-
    search plan's Round 2 outside-voice finding).
    """
    __tablename__ = "staff_secrets"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    label = Column(String(255), nullable=False)
    secret_hash = Column(String(64), nullable=False)
    bound_user_id = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("secret_hash", name="uq_staff_secrets_secret_hash"),
    )
