import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class PatientSearchAudit(Base):
    """One row per patient-search or download (D7). operator_identity is
    captured alongside the tenant API key so the audit trail names a person,
    not just "the key was used". Insert is blocking (eng-review 2026-07-20,
    outside-voice finding 6) — a failed insert must fail the request, never
    serve PHI without a log row."""
    __tablename__ = "patient_search_audit"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    operator_identity = Column(String(255), nullable=False)
    action = Column(String(20), nullable=False)  # "search" | "download"
    query_name = Column(String(255), nullable=True)
    query_dni_or_dob = Column(String(64), nullable=True)
    result_ids_shown = Column(Text, nullable=True)
    downloaded_id = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_patient_search_audit_tenant_created", "tenant_id", "created_at"),
    )
