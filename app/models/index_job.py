import enum
import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base


class IndexJobStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class IndexJob(Base):
    __tablename__ = "index_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default=IndexJobStatus.PENDING)
    chunks_total = Column(Integer, nullable=True)
    chunks_done = Column(Integer, nullable=True, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
