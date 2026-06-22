"""Add interview config language.

Revision ID: 20260622_0003
Revises: 20260622_0002
Create Date: 2026-06-22

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0003"
down_revision: str | None = "20260622_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_configs",
        sa.Column("language", sa.String(length=16), nullable=False, server_default="en"),
    )


def downgrade() -> None:
    op.drop_column("interview_configs", "language")
