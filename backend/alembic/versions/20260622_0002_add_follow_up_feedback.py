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
    dialect_name = op.get_context().dialect.name

    if dialect_name != "mysql":
        for column_name in (
            "follow_up_missing_points",
            "follow_up_weaknesses",
            "follow_up_review_suggestions",
        ):
            op.add_column(
                "interview_turns",
                sa.Column(
                    column_name,
                    sa.JSON(),
                    nullable=False,
                    server_default=sa.text("'[]'"),
                ),
            )
        return

    for column_name in (
        "follow_up_missing_points",
        "follow_up_weaknesses",
        "follow_up_review_suggestions",
    ):
        op.add_column("interview_turns", sa.Column(column_name, sa.JSON(), nullable=True))
        op.execute(
            sa.text(
                f"UPDATE interview_turns SET {column_name} = :empty_array WHERE {column_name} IS NULL"
            ).bindparams(empty_array="[]")
        )
        op.alter_column(
            "interview_turns",
            column_name,
            existing_type=sa.JSON(),
            nullable=False,
        )


def downgrade() -> None:
    op.drop_column("interview_turns", "follow_up_review_suggestions")
    op.drop_column("interview_turns", "follow_up_weaknesses")
    op.drop_column("interview_turns", "follow_up_missing_points")
    op.drop_column("interview_turns", "follow_up_feedback")
