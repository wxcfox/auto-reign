"""Drop legacy memory file metadata.

Revision ID: 20260627_0007
Revises: 20260627_0006
Create Date: 2026-06-27

"""

from collections.abc import Sequence

from alembic import context, op
import sqlalchemy as sa

revision: str = "20260627_0007"
down_revision: str | None = "20260627_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if context.is_offline_mode():
        op.execute("DROP TABLE IF EXISTS memory_files")
        return
    if _table_exists("memory_files"):
        op.drop_table("memory_files")


def downgrade() -> None:
    return
