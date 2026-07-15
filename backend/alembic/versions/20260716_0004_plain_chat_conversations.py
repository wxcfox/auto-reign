"""Allow conversations without an Agent.

Revision ID: 20260716_0004
Revises: 20260714_0003
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260716_0004"
down_revision: str | None = "20260714_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column(
            "agent_id", existing_type=sa.String(length=36), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.alter_column(
            "agent_id", existing_type=sa.String(length=36), nullable=False
        )
