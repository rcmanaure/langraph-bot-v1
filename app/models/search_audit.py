from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func

from app.models.base import Base


class SearchAudit(Base):
    """One row per lab staff search. Attributed to a named StaffSecret.label,
    not a bare Telegram user_id — a shared secret alone can't establish who
    actually searched (see the lab-staff-search plan's Round 2 finding)."""
    __tablename__ = "search_audit"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    staff_secret_label = Column(String(255), nullable=False)
    filters_used = Column(Text, nullable=False)
    result_count = Column(Integer, nullable=False)
    delivered = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
