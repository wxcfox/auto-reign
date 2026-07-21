from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.db import models


@dataclass(frozen=True)
class ClaimedDocument:
    id: str
    generation: int
    collection_id: str
    owner_user_id: int
    filename: str
    mime_type: str
    source_object_key: str
    size_bytes: int
    content_hash: str
    config_json: dict[str, object]
    processing_attempt_id: str
    retriever_type: str = "elasticsearch"


@dataclass(frozen=True)
class DocumentAttemptState:
    generation: int
    status: str
    is_active: bool
    parsed_object_key: str | None
    processing_attempt_id: str | None
    cleanup_attempt_id: str | None
    error_code: str | None


@dataclass(frozen=True)
class ReadyDocumentFilter:
    collection_id: str
    owner_user_id: int
    document_ids: tuple[str, ...] | None


class KnowledgeDocumentRepository:
    def claim_next(
        self,
        session: Session,
        *,
        stale_before: datetime,
    ) -> ClaimedDocument | None:
        document = session.scalar(
            select(models.KnowledgeDocument)
            .join(
                models.Resource,
                models.Resource.id == models.KnowledgeDocument.collection_id,
            )
            .where(
                models.KnowledgeDocument.is_active.is_(True),
                models.Resource.resource_type == "knowledge_collection",
                models.Resource.is_active.is_(True),
                models.Resource.deleted_at.is_(None),
                models.Resource.user_id == models.KnowledgeDocument.user_id,
                or_(
                    models.KnowledgeDocument.status == "queued",
                    and_(
                        models.KnowledgeDocument.status == "processing",
                        models.KnowledgeDocument.updated_at < stale_before,
                    ),
                ),
            )
            .order_by(
                models.KnowledgeDocument.updated_at,
                models.KnowledgeDocument.id,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if document is None:
            return None

        processing_attempt_id = str(uuid4())
        document.status = "processing"
        document.processing_attempt_id = processing_attempt_id
        document.updated_at = models._now()
        session.flush()
        collection = session.get(models.Resource, document.collection_id)
        if collection is None:
            raise RuntimeError("knowledge_collection_missing")
        return ClaimedDocument(
            id=document.id,
            generation=document.index_generation,
            collection_id=document.collection_id,
            owner_user_id=document.user_id,
            filename=document.name,
            mime_type=document.mime_type,
            source_object_key=document.source_object_key,
            size_bytes=document.size_bytes,
            content_hash=document.content_hash,
            config_json=dict(collection.config_json or {}),
            retriever_type=document.retriever_type,
            processing_attempt_id=processing_attempt_id,
        )

    def complete_generation(
        self,
        session: Session,
        *,
        document_id: str,
        generation: int,
        processing_attempt_id: str,
        parsed_object_key: str,
        retriever_type: str,
    ) -> bool:
        result = session.execute(
            update(models.KnowledgeDocument)
            .where(
                models.KnowledgeDocument.id == document_id,
                models.KnowledgeDocument.index_generation == generation,
                models.KnowledgeDocument.status == "processing",
                models.KnowledgeDocument.is_active.is_(True),
                models.KnowledgeDocument.processing_attempt_id
                == processing_attempt_id,
                models.KnowledgeDocument.retriever_type == retriever_type,
            )
            .values(
                status="ready",
                parsed_object_key=parsed_object_key,
                indexed_at=models._now(),
                updated_at=models._now(),
                error_code=None,
                error_message=None,
                processing_attempt_id=None,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def fail_generation(
        self,
        session: Session,
        *,
        document_id: str,
        generation: int,
        processing_attempt_id: str,
        error_code: str,
        error_message: str,
    ) -> bool:
        result = session.execute(
            update(models.KnowledgeDocument)
            .where(
                models.KnowledgeDocument.id == document_id,
                models.KnowledgeDocument.index_generation == generation,
                models.KnowledgeDocument.status == "processing",
                models.KnowledgeDocument.is_active.is_(True),
                models.KnowledgeDocument.processing_attempt_id
                == processing_attempt_id,
            )
            .values(
                status="failed",
                updated_at=models._now(),
                error_code=error_code,
                error_message=error_message[:500],
                processing_attempt_id=None,
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def get_attempt_state(
        self,
        session: Session,
        *,
        document_id: str,
    ) -> DocumentAttemptState | None:
        document = session.get(models.KnowledgeDocument, document_id)
        if document is None:
            return None
        return DocumentAttemptState(
            generation=document.index_generation,
            status=document.status,
            is_active=document.is_active,
            parsed_object_key=document.parsed_object_key,
            processing_attempt_id=document.processing_attempt_id,
            cleanup_attempt_id=document.cleanup_attempt_id,
            error_code=document.error_code,
        )

    def mark_cleanup_failed_if_inactive(
        self,
        session: Session,
        *,
        document_id: str,
        cleanup_attempt_id: str,
        message: str,
    ) -> bool:
        result = session.execute(
            update(models.KnowledgeDocument)
            .where(
                models.KnowledgeDocument.id == document_id,
                models.KnowledgeDocument.is_active.is_(False),
                models.KnowledgeDocument.cleanup_attempt_id
                == cleanup_attempt_id,
            )
            .values(
                error_code="knowledge_cleanup_failed",
                error_message=message[:500],
                cleanup_attempt_id=None,
                updated_at=models._now(),
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def clear_cleanup_error_if_inactive(
        self,
        session: Session,
        *,
        document_id: str,
        cleanup_attempt_id: str,
    ) -> bool:
        result = session.execute(
            update(models.KnowledgeDocument)
            .where(
                models.KnowledgeDocument.id == document_id,
                models.KnowledgeDocument.is_active.is_(False),
                models.KnowledgeDocument.cleanup_attempt_id
                == cleanup_attempt_id,
            )
            .values(
                error_code=None,
                error_message=None,
                cleanup_attempt_id=None,
                updated_at=models._now(),
            )
            .execution_options(synchronize_session=False)
        )
        return result.rowcount == 1

    def list_for_collection(
        self,
        session: Session,
        *,
        collection_id: str,
        include_inactive: bool = False,
    ) -> list[models.KnowledgeDocument]:
        query = select(models.KnowledgeDocument).where(
            models.KnowledgeDocument.collection_id == collection_id
        )
        if not include_inactive:
            query = query.where(models.KnowledgeDocument.is_active.is_(True))
        return list(
            session.scalars(
                query.order_by(
                    models.KnowledgeDocument.created_at.desc(),
                    models.KnowledgeDocument.id,
                )
            )
        )

    def list_ready_for_scopes(
        self,
        session: Session,
        *,
        scopes: tuple[ReadyDocumentFilter, ...],
    ) -> list[models.KnowledgeDocument]:
        """Materialize every configured Collection/owner pair in one snapshot."""
        if not scopes:
            return []

        paired_conditions = []
        for scope in scopes:
            conditions = [
                models.KnowledgeDocument.collection_id == scope.collection_id,
                models.KnowledgeDocument.user_id == scope.owner_user_id,
            ]
            if scope.document_ids is not None:
                conditions.append(models.KnowledgeDocument.id.in_(scope.document_ids))
            paired_conditions.append(and_(*conditions))

        return list(
            session.scalars(
                select(models.KnowledgeDocument)
                .where(
                    models.KnowledgeDocument.is_active.is_(True),
                    models.KnowledgeDocument.status == "ready",
                    or_(*paired_conditions),
                )
                .order_by(
                    models.KnowledgeDocument.collection_id,
                    models.KnowledgeDocument.user_id,
                    models.KnowledgeDocument.created_at,
                    models.KnowledgeDocument.id,
                )
                .execution_options(populate_existing=True)
            )
        )

    def get(
        self,
        session: Session,
        *,
        document_id: str,
    ) -> models.KnowledgeDocument | None:
        return session.get(models.KnowledgeDocument, document_id)

    def get_for_update(
        self,
        session: Session,
        *,
        document_id: str,
    ) -> models.KnowledgeDocument | None:
        return session.scalar(
            select(models.KnowledgeDocument)
            .where(models.KnowledgeDocument.id == document_id)
            .execution_options(populate_existing=True)
            .with_for_update()
        )

    def lock_active_references(
        self,
        session: Session,
        *,
        collection_id: str,
        owner_user_id: int,
        document_ids: tuple[str, ...],
    ) -> list[models.KnowledgeDocument]:
        if not document_ids:
            return []
        return list(
            session.scalars(
                select(models.KnowledgeDocument)
                .where(
                    models.KnowledgeDocument.collection_id == collection_id,
                    models.KnowledgeDocument.user_id == owner_user_id,
                    models.KnowledgeDocument.id.in_(document_ids),
                    models.KnowledgeDocument.is_active.is_(True),
                )
                .order_by(models.KnowledgeDocument.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        )

    def queue(
        self,
        session: Session,
        document: models.KnowledgeDocument,
    ) -> None:
        document.status = "queued"
        document.processing_attempt_id = None
        document.cleanup_attempt_id = None
        document.error_code = None
        document.error_message = None
        document.updated_at = models._now()
        session.flush()

    def begin_cleanup(
        self,
        session: Session,
        document: models.KnowledgeDocument,
        *,
        cleanup_attempt_id: str,
    ) -> None:
        document.is_active = False
        document.processing_attempt_id = None
        document.cleanup_attempt_id = cleanup_attempt_id
        document.error_code = "knowledge_cleanup_pending"
        document.error_message = None
        document.updated_at = models._now()
        session.flush()
