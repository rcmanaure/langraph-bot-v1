from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    job_id = Column(UUID(as_uuid=True), ForeignKey("index_jobs.id", ondelete="SET NULL"), nullable=True)
    namespace = Column(String(100), nullable=False)
    source = Column(String(255), nullable=False)
    page = Column(Integer, default=0)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(1536))
    chunk_type = Column(String(20), nullable=True)
    metadata_ = Column("metadata", JSONB, nullable=True, server_default="{}")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index(
            "ix_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 128},
        ),
        Index("ix_document_chunks_tenant_ns", "tenant_id", "namespace"),
    )
