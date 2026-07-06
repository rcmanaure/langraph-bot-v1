from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, DateTime, String, func

from app.models.base import Base


class EmbeddingCache(Base):
    """Content-addressed embedding cache — key is a hash of model+text.

    Skips paying for (and waiting on) an embedding call whenever the exact
    same text was embedded before. The dominant case: re-uploading a catalog
    file re-embeds every line even if only one item changed (indexer deletes
    and re-inserts all chunks on re-upload) — unchanged items hit this cache
    instead of re-calling the embeddings API.
    """

    __tablename__ = "embedding_cache"

    key = Column(String(64), primary_key=True)  # sha256 hex digest
    embedding = Column(Vector(1536), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
