import os

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base  # noqa: F401 — ensures all models are registered

_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://ragbot:ragbot@localhost:5432/ragbot")
if _url.startswith("postgresql://"):
    _url = _url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(
    _url,
    echo=False,
    pool_size=int(os.environ.get("DB_POOL_SIZE", "5")),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", "5")),
    pool_pre_ping=True,
)

AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
