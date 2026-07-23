"""Create the Task/Subtask baseline.

Revision ID: 20260722_0001
Revises:
Create Date: 2026-07-22
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision: str = "20260722_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BIGINT_TYPE = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
LONGBLOB_TYPE = mysql.LONGBLOB().with_variant(sa.LargeBinary(), "sqlite")
LONGTEXT_TYPE = mysql.LONGTEXT().with_variant(sa.Text(), "sqlite")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("username", sa.String(length=80), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("seed_initialized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credential_bootstrap_status", sa.String(length=16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "resources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("id", "user_id", name="uq_resources_id_owner"),
        sa.UniqueConstraint(
            "user_id",
            "resource_type",
            "name",
            name="uq_resources_owner_type_name",
        ),
    )
    op.create_index("ix_resources_user_id", "resources", ["user_id"])
    op.create_index("ix_resources_resource_type", "resources", ["resource_type"])

    op.create_table(
        "knowledge_documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("source_object_key", sa.String(length=1024), nullable=False),
        sa.Column("parsed_object_key", sa.String(length=1024), nullable=True),
        sa.Column("mime_type", sa.String(length=160), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("index_generation", sa.Integer(), nullable=False),
        sa.Column(
            "retriever_type",
            sa.String(length=32),
            nullable=False,
            server_default="elasticsearch",
        ),
        sa.Column("processing_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("cleanup_attempt_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["collection_id", "user_id"],
            ["resources.id", "resources.user_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_knowledge_documents_collection_id",
        "knowledge_documents",
        ["collection_id"],
    )
    op.create_index("ix_knowledge_documents_user_id", "knowledge_documents", ["user_id"])

    op.create_table(
        "tasks",
        sa.Column("id", BIGINT_TYPE, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("model_override_json", sa.JSON(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["resources.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tasks_agent_id", "tasks", ["agent_id"])
    op.create_index("ix_tasks_status", "tasks", ["status"])
    op.create_index("ix_tasks_user_id", "tasks", ["user_id"])

    op.create_table(
        "subtasks",
        sa.Column("id", BIGINT_TYPE, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("task_id", BIGINT_TYPE, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("message_id", BIGINT_TYPE, nullable=False),
        sa.Column("parent_id", BIGINT_TYPE, nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("prompt", LONGTEXT_TYPE, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subtasks_message_id", "subtasks", ["message_id"])
    op.create_index("ix_subtasks_status", "subtasks", ["status"])
    op.create_index("ix_subtasks_task_id", "subtasks", ["task_id"])
    op.create_index("ix_subtasks_user_id", "subtasks", ["user_id"])

    op.create_table(
        "subtask_contexts",
        sa.Column("id", BIGINT_TYPE, autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subtask_id", BIGINT_TYPE, nullable=False),
        sa.Column("context_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("binary_data", LONGBLOB_TYPE, nullable=True),
        sa.Column("image_base64", LONGTEXT_TYPE, nullable=True),
        sa.Column("extracted_text", LONGTEXT_TYPE, nullable=True),
        sa.Column("text_length", sa.Integer(), nullable=False),
        sa.Column("mime_type", sa.String(length=160), nullable=True),
        sa.Column("file_extension", sa.String(length=32), nullable=True),
        sa.Column("file_size", BIGINT_TYPE, nullable=True),
        sa.Column("type_data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_subtask_contexts_context_type", "subtask_contexts", ["context_type"])
    op.create_index("ix_subtask_contexts_subtask_id", "subtask_contexts", ["subtask_id"])
    op.create_index("ix_subtask_contexts_user_id", "subtask_contexts", ["user_id"])


def downgrade() -> None:
    op.drop_table("subtask_contexts")
    op.drop_table("subtasks")
    op.drop_table("tasks")
    op.drop_table("knowledge_documents")
    op.drop_table("resources")
    op.drop_table("users")
