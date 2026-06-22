from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from app.models.base import Base


class WaServiceWindow(Base):
    """Tracks the 24h WhatsApp service window per tenant+user."""
    __tablename__ = "wa_service_windows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    user_id = Column(String(100), nullable=False)
    last_user_message_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_wa_window_tenant_user"),
    )
