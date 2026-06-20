"""Create the initial application schema.

Revision ID: 20260620_0001
Revises:
Create Date: 2026-06-20

"""
from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260620_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("collection", sa.String(length=120), nullable=False),
        sa.Column("source_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("file_type", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("knowledge_points", sa.JSON(), nullable=False),
        sa.Column("weakness_candidates", sa.JSON(), nullable=False),
        sa.Column("analysis_status", sa.String(length=32), nullable=False),
        sa.Column("index_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=128), nullable=False),
        sa.Column("vector_collection", sa.String(length=120), nullable=False),
        sa.Column("vector_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("vector_id"),
    )
    op.create_table(
        "interview_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_company", sa.String(length=255), nullable=False),
        sa.Column("target_role", sa.String(length=255), nullable=False),
        sa.Column("job_description", sa.Text(), nullable=False),
        sa.Column("extra_prompt", sa.Text(), nullable=False),
        sa.Column("mode", sa.String(length=64), nullable=False),
        sa.Column("chat_model_provider", sa.String(length=64), nullable=False),
        sa.Column("chat_model", sa.String(length=120), nullable=False),
        sa.Column("target_rounds", sa.Integer(), nullable=False),
        sa.Column("is_last_used", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "interview_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("config_id", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_round", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("report_path", sa.String(length=1024), nullable=True),
        sa.ForeignKeyConstraint(["config_id"], ["interview_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "interview_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("missing_points", sa.JSON(), nullable=False),
        sa.Column("follow_up_question", sa.Text(), nullable=True),
        sa.Column("follow_up_answer", sa.Text(), nullable=True),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("review_suggestions", sa.JSON(), nullable=False),
        sa.Column("retrieved_context_refs", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["interview_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "reports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("report_path", sa.String(length=1024), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("weaknesses", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["interview_sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "memory_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.String(length=1024), nullable=False),
        sa.Column("summary_hash", sa.String(length=128), nullable=False),
        sa.Column("last_indexed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind"),
    )


def downgrade() -> None:
    op.drop_table("memory_files")
    op.drop_table("reports")
    op.drop_table("interview_turns")
    op.drop_table("interview_sessions")
    op.drop_table("interview_configs")
    op.drop_table("document_chunks")
    op.drop_table("documents")
