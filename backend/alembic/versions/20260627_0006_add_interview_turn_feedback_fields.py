"""Add structured answer feedback fields to interview turns.

Revision ID: 20260627_0006
Revises: 20260625_0005
Create Date: 2026-06-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260627_0006"
down_revision: str | None = "20260625_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "interview_turns",
        sa.Column("better_answer", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "interview_turns",
        sa.Column("mastery_change", sa.String(length=64), nullable=False, server_default="unchanged"),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "should_write_weakness",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "should_write_high_frequency",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    dialect_name = op.get_context().dialect.name
    if dialect_name != "mysql":
        op.add_column(
            "interview_turns",
            sa.Column("tested_points", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
        return

    op.add_column("interview_turns", sa.Column("tested_points", sa.JSON(), nullable=True))
    op.execute(
        sa.text("UPDATE interview_turns SET tested_points = :empty_array WHERE tested_points IS NULL")
        .bindparams(empty_array="[]")
    )
    op.alter_column(
        "interview_turns",
        "tested_points",
        existing_type=sa.JSON(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("interview_turns", "tested_points")
    op.drop_column("interview_turns", "should_write_high_frequency")
    op.drop_column("interview_turns", "should_write_weakness")
    op.drop_column("interview_turns", "mastery_change")
    op.drop_column("interview_turns", "better_answer")
