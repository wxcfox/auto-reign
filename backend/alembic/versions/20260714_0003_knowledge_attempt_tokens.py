"""Add Knowledge Worker and cleanup attempt tokens.

Revision ID: 20260714_0003
Revises: 20260713_0002
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260714_0003"
down_revision: str | None = "20260713_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column("processing_attempt_id", sa.String(length=36), nullable=True),
    )
    op.add_column(
        "knowledge_documents",
        sa.Column("cleanup_attempt_id", sa.String(length=36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "cleanup_attempt_id")
    op.drop_column("knowledge_documents", "processing_attempt_id")
