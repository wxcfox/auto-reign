"""Add conversation rename and soft-delete fields.

Revision ID: 20260701_0010
Revises: 20260627_0009
Create Date: 2026-07-01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260701_0010"
down_revision: str | None = "20260627_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("interview_sessions", sa.Column("title", sa.String(length=255), nullable=True))
    op.add_column(
        "interview_sessions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "learning_sessions", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("learning_sessions", "deleted_at")
    op.drop_column("interview_sessions", "deleted_at")
    op.drop_column("interview_sessions", "title")
