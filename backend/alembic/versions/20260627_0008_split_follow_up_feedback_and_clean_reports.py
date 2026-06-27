"""Split follow-up feedback fields and clean legacy report paths.

Revision ID: 20260627_0008
Revises: 20260627_0007
Create Date: 2026-06-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260627_0008"
down_revision: str | None = "20260627_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _legacy_absolute_path_filter(column_name: str) -> str:
    return f"{column_name} LIKE '/%' OR {column_name} LIKE '_:%'"


def _add_json_array_column(column_name: str) -> None:
    dialect_name = op.get_context().dialect.name
    if dialect_name != "mysql":
        op.add_column(
            "interview_turns",
            sa.Column(column_name, sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
        return

    op.add_column("interview_turns", sa.Column(column_name, sa.JSON(), nullable=True))
    op.execute(
        sa.text(f"UPDATE interview_turns SET {column_name} = :empty_array WHERE {column_name} IS NULL")
        .bindparams(empty_array="[]")
    )
    op.alter_column(
        "interview_turns",
        column_name,
        existing_type=sa.JSON(),
        nullable=False,
    )


def upgrade() -> None:
    op.add_column(
        "interview_turns",
        sa.Column("follow_up_better_answer", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "follow_up_mastery_change",
            sa.String(length=64),
            nullable=False,
            server_default="unchanged",
        ),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "follow_up_should_write_weakness",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "interview_turns",
        sa.Column(
            "follow_up_should_write_high_frequency",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    _add_json_array_column("follow_up_tested_points")

    op.execute(
        sa.text(
            "UPDATE interview_sessions SET report_path = NULL "
            f"WHERE {_legacy_absolute_path_filter('report_path')}"
        )
    )
    op.execute(
        sa.text(f"DELETE FROM reports WHERE {_legacy_absolute_path_filter('report_path')}")
    )


def downgrade() -> None:
    op.drop_column("interview_turns", "follow_up_tested_points")
    op.drop_column("interview_turns", "follow_up_should_write_high_frequency")
    op.drop_column("interview_turns", "follow_up_should_write_weakness")
    op.drop_column("interview_turns", "follow_up_mastery_change")
    op.drop_column("interview_turns", "follow_up_better_answer")
