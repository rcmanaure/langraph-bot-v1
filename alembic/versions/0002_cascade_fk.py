"""Add ON DELETE CASCADE to tenant FKs

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-30

Drops and re-creates the four FK constraints that reference tenants(id)
without an ondelete action, replacing them with ON DELETE CASCADE so that
deleting a tenant atomically removes all dependent rows.

LangGraph checkpoint tables (checkpoints, checkpoint_blobs, checkpoint_writes)
are NOT owned by Alembic and have no FK to tenants — their cleanup is handled
at runtime in the DELETE /admin/tenants/{slug} endpoint via thread_id LIKE.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Tables whose tenant_id FK needs CASCADE; maps table → constraint name.
# PostgreSQL auto-names unnamed FKs as {table}_{column}_fkey.
_CASCADE_FKS = [
    ("index_jobs",        "index_jobs_tenant_id_fkey"),
    ("document_chunks",   "document_chunks_tenant_id_fkey"),
    ("conversation_audit","conversation_audit_tenant_id_fkey"),
    ("wa_service_windows","wa_service_windows_tenant_id_fkey"),
]


def upgrade() -> None:
    for table, constraint in _CASCADE_FKS:
        # DROP with IF EXISTS so re-running on a partially-migrated DB is safe.
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE"
        )


def downgrade() -> None:
    for table, constraint in _CASCADE_FKS:
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
            f"FOREIGN KEY (tenant_id) REFERENCES tenants(id)"
        )
