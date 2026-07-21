"""Persist the Retriever selected for each Knowledge Document generation.

Revision ID: 20260720_0005
Revises: 20260716_0004
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260720_0005"
down_revision: str | None = "20260716_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "knowledge_documents",
        sa.Column(
            "retriever_type",
            sa.String(length=32),
            nullable=False,
            server_default="elasticsearch",
        ),
    )
    # The previous schema persisted nullable score thresholds and did not include
    # Retriever settings. Historical Collection configuration is intentionally
    # reset to the new baseline instead of adding a runtime compatibility path.
    op.execute(
        sa.text(
            """
            UPDATE resources
            SET config_json = '{
                "retriever_type": "elasticsearch",
                "retrieval_mode": "vector",
                "chunk_size": 900,
                "chunk_overlap": 120,
                "top_k": 5,
                "score_threshold": 0.5,
                "vector_weight": 0.7,
                "keyword_weight": 0.3
            }', updated_at = CURRENT_TIMESTAMP
            WHERE resource_type = 'knowledge_collection'
            """
        )
    )
    # Existing projections were created by the pre-Retriever Qdrant-only pipeline.
    # They cannot satisfy the new default Elasticsearch binding. Requeue every active
    # document with a fresh generation so no row remains falsely ready while its
    # selected Retriever has no corresponding index. Source objects remain authoritative.
    knowledge_documents = sa.table(
        "knowledge_documents",
        sa.column("is_active", sa.Boolean()),
        sa.column("status", sa.String(length=32)),
        sa.column("index_generation", sa.Integer()),
        sa.column("parsed_object_key", sa.String(length=1024)),
        sa.column("indexed_at", sa.DateTime(timezone=True)),
        sa.column("processing_attempt_id", sa.String(length=36)),
        sa.column("cleanup_attempt_id", sa.String(length=36)),
        sa.column("error_code", sa.String(length=80)),
        sa.column("error_message", sa.Text()),
        sa.column("retriever_type", sa.String(length=32)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.execute(
        knowledge_documents.update()
        .where(knowledge_documents.c.is_active.is_(True))
        .values(
            status="queued",
            index_generation=knowledge_documents.c.index_generation + 1,
            parsed_object_key=None,
            indexed_at=None,
            processing_attempt_id=None,
            cleanup_attempt_id=None,
            error_code=None,
            error_message=None,
            retriever_type="elasticsearch",
            updated_at=sa.func.now(),
        )
    )


def downgrade() -> None:
    op.drop_column("knowledge_documents", "retriever_type")
