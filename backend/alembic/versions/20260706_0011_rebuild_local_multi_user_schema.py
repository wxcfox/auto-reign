"""Rebuild schema for local multi-user isolation.

Revision ID: 20260706_0011
Revises: 20260701_0010
Create Date: 2026-07-06
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260706_0011"
down_revision: str | None = "20260701_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_TABLES = (
    "processing_jobs",
    "reports",
    "learning_messages",
    "learning_sessions",
    "interview_turns",
    "interview_sessions",
    "interview_configs",
    "artifacts",
    "workspace_settings",
)


def _legacy_tables_with_rows() -> list[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    non_empty_tables: list[str] = []
    for table_name in LEGACY_TABLES:
        if not inspector.has_table(table_name):
            continue
        has_row = bind.execute(sa.text(f"SELECT 1 FROM {table_name} LIMIT 1")).first()
        if has_row is not None:
            non_empty_tables.append(table_name)
    return non_empty_tables


def _drop_existing_legacy_tables() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table_name in LEGACY_TABLES:
        if inspector.has_table(table_name):
            op.drop_table(table_name)


def _drop_legacy_tables_offline() -> None:
    for table_name in LEGACY_TABLES:
        op.drop_table(table_name)


def _create_target_tables() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("relative_path", sa.String(length=512), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "relative_path", name="uq_artifacts_user_path"),
    )
    op.create_index("ix_artifacts_kind", "artifacts", ["kind"])
    op.create_index("ix_artifacts_user_id", "artifacts", ["user_id"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("summary_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_conversations_id_user"),
    )
    op.create_index("ix_conversations_kind", "conversations", ["kind"])
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("message_type", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id", "user_id"],
            ["conversations.id", "conversations.user_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    op.create_index("ix_messages_user_id", "messages", ["user_id"])


def upgrade() -> None:
    context = op.get_context()
    if context.as_sql:
        _drop_legacy_tables_offline()
    else:
        non_empty_tables = _legacy_tables_with_rows()
        if non_empty_tables:
            table_list = ", ".join(non_empty_tables)
            raise RuntimeError(
                "Run ./reset-data.sh explicitly before upgrading; "
                f"legacy tables contain rows: {table_list}"
            )
        _drop_existing_legacy_tables()

    _create_target_tables()


def downgrade() -> None:
    op.drop_index("ix_messages_user_id", table_name="messages")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_index("ix_conversations_kind", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_artifacts_user_id", table_name="artifacts")
    op.drop_index("ix_artifacts_kind", table_name="artifacts")
    op.drop_table("artifacts")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
