"""Add follow-up feedback fields to interview turns.

Revision ID: 20260622_0002
Revises: 20260620_0001
Create Date: 2026-06-22

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260622_0002"
down_revision: str | None = "20260620_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("interview_turns", sa.Column("follow_up_feedback", sa.Text(), nullable=True))
    op.add_column(
        "interview_turns",
        sa.Column(
            "follow_up_missing_points",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "follow_up_weaknesses",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "follow_up_review_suggestions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("interview_turns", "follow_up_review_suggestions")
    op.drop_column("interview_turns", "follow_up_weaknesses")
    op.drop_column("interview_turns", "follow_up_missing_points")
    op.drop_column("interview_turns", "follow_up_feedback")
