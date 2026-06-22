"""baseline

Revision ID: 0001
Revises:
Create Date: 2026-06-21

NOTE: LangGraph tables (langgraph_checkpoints, langgraph_writes, langgraph_migrations)
are NOT created here — they are created idempotently by checkpointer.setup() in the
FastAPI lifespan after this migration runs.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(64), unique=True, nullable=False),
        sa.Column("api_key_hash", sa.String(64), unique=True, nullable=False),
        sa.Column("webhook_secret", sa.String(64), nullable=False),
        sa.Column("bot_token", sa.String(128), unique=True, nullable=False),
        sa.Column("plan", sa.String(32), server_default="free"),
        sa.Column("expertise_area", sa.String(255), nullable=True),
        sa.Column("contact_url", sa.String(512), nullable=True),
        sa.Column("example_questions", sa.JSON(), nullable=True),
        sa.Column("operator_chat_id", sa.String(64), nullable=True),
        sa.Column("web_search_enabled", sa.Boolean(), server_default="false"),
        sa.Column("doc_structure_summary", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("wa_phone_number_id", sa.String(100), nullable=True),
        sa.Column("wa_access_token", sa.String(500), nullable=True),
        sa.Column("wa_app_secret", sa.String(100), nullable=True),
        sa.Column("wa_verify_token", sa.String(100), nullable=True),
        sa.Column("channels", sa.Text(), server_default="telegram"),
        sa.Column("portal_password_hash", sa.String(128), nullable=True),
    )
    op.create_index("ix_tenants_slug", "tenants", ["slug"])

    op.create_table(
        "index_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("chunks_total", sa.Integer(), nullable=True),
        sa.Column("chunks_done", sa.Integer(), server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_index_jobs_tenant", "index_jobs", ["tenant_id"])

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("index_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("namespace", sa.String(100), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("page", sa.Integer(), server_default="0"),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", sa.Text(), nullable=True),  # replaced by vector below
        sa.Column("chunk_type", sa.String(20), nullable=True),
        sa.Column("metadata", postgresql.JSONB(), server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # Use raw SQL for vector column type (not natively in sqlalchemy column defs)
    op.execute("ALTER TABLE document_chunks ALTER COLUMN embedding TYPE vector(1536) USING NULL")
    op.create_index("ix_document_chunks_tenant_ns", "document_chunks", ["tenant_id", "namespace"])
    op.execute(
        "CREATE INDEX ix_embedding_hnsw ON document_chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )

    op.create_table(
        "conversation_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(20), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=True),
        sa.Column("bot_response", sa.Text(), nullable=True),
        sa.Column("langsmith_trace_url", sa.String(512), nullable=True),
        sa.Column("interrupt_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_conversation_audit_thread", "conversation_audit", ["thread_id"])
    op.create_index("ix_conversation_audit_tenant_created", "conversation_audit", ["tenant_id", "created_at"])
    op.execute(
        "CREATE INDEX ix_conversation_audit_interrupt_pending "
        "ON conversation_audit (interrupt_started_at) "
        "WHERE expired_at IS NULL AND interrupt_started_at IS NOT NULL"
    )

    op.create_table(
        "wa_service_windows",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("user_id", sa.String(100), nullable=False),
        sa.Column("last_user_message_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_wa_window_tenant_user"),
    )


def downgrade() -> None:
    op.drop_table("wa_service_windows")
    op.drop_table("conversation_audit")
    op.drop_table("document_chunks")
    op.drop_table("index_jobs")
    op.drop_table("tenants")
