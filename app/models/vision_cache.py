from sqlalchemy import Column, DateTime, String, func

from app.models.base import Base


class VisionCache(Base):
    """Content-addressed cache of vision extraction results — key hashes
    model+caption+image bytes.

    Same photo resent (common: users retake/reforward the same order) would
    otherwise re-pay the full two-call extraction+verification pipeline every
    time. Only deterministic content judgments are cached here (legible/not,
    verified/rejected, the extracted question) — transient API failures are
    never cached, since a retry might succeed once the API recovers.
    """

    __tablename__ = "vision_cache"

    key = Column(String(64), primary_key=True)  # sha256 hex digest
    result = Column(String, nullable=False)  # extracted price_question, or the VISION_UNCERTAIN sentinel
    created_at = Column(DateTime(timezone=True), server_default=func.now())
