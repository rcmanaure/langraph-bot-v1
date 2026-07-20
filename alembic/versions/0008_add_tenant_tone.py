"""Add tone column to tenants

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20

Lets a tenant opt into a formal register (e.g. a clinical lab talking to
patients) instead of the default casual WhatsApp tone, without changing
the prompt globally for every tenant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("tone", sa.String(16), nullable=False, server_default="casual"),
    )


def downgrade() -> None:
    op.drop_column("tenants", "tone")
