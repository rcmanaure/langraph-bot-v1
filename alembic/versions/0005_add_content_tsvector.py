"""Add tsvector column + GIN index for hybrid (keyword) search

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-06

Dense-only vector search misses exact-match queries (product codes, item
names) — see RAG pipeline audit. This adds a generated tsvector column so
retrieve_chunks can fuse keyword and vector search results via Reciprocal
Rank Fusion. No re-embedding required: it's derived from the existing
`content` column. Note: ADD COLUMN ... GENERATED ALWAYS ... STORED forces a
full table rewrite to backfill existing rows — fine at current scale
(document_chunks is capped at 10k rows/tenant per PLAN_LIMITS), but worth
knowing if this table grows much larger before a future migration.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0005"
down_revision: Union[str, Sequence[str], None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE document_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS (to_tsvector('spanish', content)) STORED
        """
    )
    op.execute(
        """
        CREATE INDEX ix_document_chunks_content_tsv
        ON document_chunks
        USING gin (content_tsv)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
