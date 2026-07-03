"""Unique index backing idempotent interrupt-audit inserts

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-03

interrupt_node() does a SELECT-then-INSERT check to avoid writing a duplicate
conversation_audit row when interrupt() re-runs the node from the top on
resume. That app-level check has a TOCTOU race: two concurrent invocations of
the same thread (e.g. a redelivered WhatsApp/Telegram webhook — see the
"Always return 200 fast" comment in app/channels/whatsapp.py acknowledging
duplicate delivery happens) can both pass the SELECT before either commits.

This adds a unique partial index on thread_id as the DB-level backstop:
at most one open (expired_at IS NULL, interrupt_started_at IS NOT NULL) audit
row can exist per thread. interrupt_node() catches the resulting
IntegrityError and treats it as "lost the race, row already exists" rather
than a genuine failure.

Deliberately a separate index from ix_conversation_audit_interrupt_pending
(0001_baseline), which indexes interrupt_started_at for listing/ordering
pending interrupts and is not unique.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_conversation_audit_open_interrupt "
        "ON conversation_audit (thread_id) "
        "WHERE expired_at IS NULL AND interrupt_started_at IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_conversation_audit_open_interrupt")
