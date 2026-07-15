"""Add parsed attachment integrity metadata.

Revision ID: 20260713_0002
Revises: 20260713_0001
Create Date: 2026-07-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260713_0002"
down_revision: str | None = "20260713_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column("parsed_size_bytes", sa.Integer(), nullable=True),
    )
    op.add_column(
        "attachments",
        sa.Column("parsed_content_hash", sa.String(length=128), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("attachments", "parsed_content_hash")
    op.drop_column("attachments", "parsed_size_bytes")
