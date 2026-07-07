"""Add vision_cache table

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-07

Content-addressed cache for vision extraction results — same photo resent
(common: users retake/reforward the same order) would otherwise re-pay the
full two-call extraction+verification pipeline every time.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "vision_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("result", sa.String, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("vision_cache")
