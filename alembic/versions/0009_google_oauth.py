"""Add Google OAuth columns to tenants + patient_search_audit table

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20

Additive migration for the Gmail/Drive patient-results feature
(plan-ceo-review 2026-07-19, D1-D8). tenants gains 2 nullable columns
(opt-in per tenant, no backfill needed). patient_search_audit is a new,
independent table — no FK circularity, one row per search/download.

D7: operator_identity is captured alongside the tenant API key so the audit
trail names a person, not just "the key was used".
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("google_refresh_token", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("google_connected_email", sa.String(255), nullable=True))

    op.create_table(
        "patient_search_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Integer, sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("operator_identity", sa.String(255), nullable=False),
        sa.Column("action", sa.String(20), nullable=False),  # "search" | "download"
        sa.Column("query_name", sa.String(255), nullable=True),
        sa.Column("query_dni_or_dob", sa.String(64), nullable=True),
        sa.Column("result_ids_shown", sa.Text(), nullable=True),
        sa.Column("downloaded_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_patient_search_audit_tenant_created",
        "patient_search_audit",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_patient_search_audit_tenant_created", table_name="patient_search_audit")
    op.drop_table("patient_search_audit")
    op.drop_column("tenants", "google_connected_email")
    op.drop_column("tenants", "google_refresh_token")
