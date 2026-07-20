"""Replace tenants.tone enum with free-text tone_description

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-20

The casual/formal enum only covers two registers. A multi-tenant bot spans
arbitrary business types (clinic, gym, bakery...) where the right voice
can't be captured by a fixed preset list without a code change + deploy
per new vertical. Free text lets the operator describe the voice directly
per tenant.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CASUAL_TEXT = (
    "cálido y cercano, como una persona del negocio respondiendo por chat. "
    "Nada de lenguaje robótico, emojis casuales están bien"
)
_FORMAL_TEXT = (
    "profesional, claro y respetuoso — como el personal de un laboratorio clínico "
    "atendiendo a un paciente, no un comercio. Sin emojis casuales ni lenguaje de venta"
)


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("tone_description", sa.Text(), nullable=False, server_default=_CASUAL_TEXT),
    )
    op.execute(
        sa.text("UPDATE tenants SET tone_description = :formal WHERE tone = 'formal'").bindparams(
            formal=_FORMAL_TEXT
        )
    )
    op.drop_column("tenants", "tone")


def downgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column("tone", sa.String(16), nullable=False, server_default="casual"),
    )
    op.execute(
        sa.text("UPDATE tenants SET tone = 'formal' WHERE tone_description = :formal").bindparams(
            formal=_FORMAL_TEXT
        )
    )
    op.drop_column("tenants", "tone_description")
