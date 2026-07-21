"""Add patient_index table

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-20

Minimal patient directory used to disambiguate name+DNI/DOB matches before
searching Gmail/Drive (D4/D5, plan-ceo-review 2026-07-19). Populated via
admin CRUD (T3b) — no automated import in phase 1.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_index",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("patient_name", sa.String(255), nullable=False),
        sa.Column("dni_or_dob", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_patient_index_tenant_name",
        "patient_index",
        ["tenant_id", "patient_name"],
    )


def downgrade() -> None:
    op.drop_index("ix_patient_index_tenant_name", table_name="patient_index")
    op.drop_table("patient_index")
