"""Add embedding_cache table

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-06

Content-addressed cache for embedding vectors — see RAG pipeline audit.
Re-uploading a catalog file re-embeds every line even when only one item
changed (indexer deletes and re-inserts all chunks on re-upload); this table
lets unchanged text skip the embeddings API call entirely.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "embedding_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("embedding", Vector(1536), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("embedding_cache")
