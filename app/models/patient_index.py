import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class PatientIndex(Base):
    """Minimal patient directory for disambiguating name+DNI/DOB matches
    before searching Gmail/Drive (D4/D5). Populated via admin CRUD (T3b)."""
    __tablename__ = "patient_index"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    patient_name = Column(String(255), nullable=False)
    dni_or_dob = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_patient_index_tenant_name", "tenant_id", "patient_name"),
    )
