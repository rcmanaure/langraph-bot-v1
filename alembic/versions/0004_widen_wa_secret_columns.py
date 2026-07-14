"""Widen wa_access_token/wa_app_secret to TEXT

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-03

Fernet-encrypted values (base64 ciphertext + IV + HMAC + timestamp overhead)
run well past the raw token length. A real Meta long-lived access token
(~326 chars) encrypts to >500 chars, overflowing wa_access_token's
VARCHAR(500) with StringDataRightTruncationError on save. wa_app_secret's
VARCHAR(100) has the same problem for any secret longer than a few chars.
Both become TEXT to remove the ceiling entirely.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tenants", "wa_access_token", type_=sa.Text())
    op.alter_column("tenants", "wa_app_secret", type_=sa.Text())


def downgrade() -> None:
    op.alter_column("tenants", "wa_access_token", type_=sa.String(500))
    op.alter_column("tenants", "wa_app_secret", type_=sa.String(100))
