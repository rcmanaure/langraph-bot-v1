"""Add staff_secrets, integration_credentials, search_audit tables

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-15

Phase 1 of the lab staff search feature (see
~/.gstack/projects/rcmanaure-langraph-bot-v1/ceo-plans/2026-07-15-lab-staff-search.md).
All three tables FK to tenants(id) with ON DELETE CASCADE from the start,
matching the pattern 0002_cascade_fk.py established for every other
tenant-owned table (no need for a follow-up cascade migration).
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "staff_secrets",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer,
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("bound_user_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("secret_hash", name="uq_staff_secrets_secret_hash"),
    )
    op.create_index("ix_staff_secrets_tenant_id", "staff_secrets", ["tenant_id"])

    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer,
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("integration_type", sa.String(32), nullable=False),
        sa.Column("encrypted_credentials", sa.Text, nullable=False),
        sa.Column("metadata", sa.JSON, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  onupdate=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "integration_type",
                             name="uq_integration_credentials_tenant_type"),
    )
    op.create_index("ix_integration_credentials_tenant_id", "integration_credentials", ["tenant_id"])

    op.create_table(
        "search_audit",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("tenant_id", sa.Integer,
                  sa.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False),
        sa.Column("staff_secret_label", sa.String(255), nullable=False),
        sa.Column("filters_used", sa.Text, nullable=False),
        sa.Column("result_count", sa.Integer, nullable=False),
        sa.Column("delivered", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_search_audit_tenant_id", "search_audit", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("search_audit")
    op.drop_table("integration_credentials")
    op.drop_table("staff_secrets")
