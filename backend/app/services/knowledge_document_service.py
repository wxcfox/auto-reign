from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from typing import Protocol
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.core.limits import DEFAULT_KNOWLEDGE_MAX_PARSED_CHARS
from app.core.errors import conflict, not_found, service_unavailable
from app.db import models
from app.db.session import session_scope
from app.repositories.knowledge_document_repository import (
    KnowledgeDocumentRepository,
)
from app.repositories.resource_repository import ResourceRepository
from app.schemas.agents import AgentConfig
from app.services.document_operation_coordinator import DocumentOperationCoordinator
from app.services.knowledge_collection_service import KnowledgeCollectionService
from app.services.knowledge_vector_store import DocumentVectorScope
from app.services.upload_validation_service import ValidatedUpload
from app.storage.object_store import (
    ObjectConflict,
    ObjectStore,
    ObjectStoreError,
    StoredObject,
)


logger = logging.getLogger(__name__)


class KnowledgeContentUnavailable(RuntimeError):
    pass


class KnowledgeCleanupError(RuntimeError):
    pass


@dataclass(frozen=True)
class InactiveDocumentCleanup:
    id: str
    user_id: int
    collection_id: str


class _KnowledgeCleanupVectorStore(Protocol):
    def delete_document(self, scope: DocumentVectorScope) -> None: ...


def read_parsed_text(
    object_store: ObjectStore,
    *,
    object_key: str,
    max_parsed_chars: int,
) -> str:
    try:
        stored = object_store.get(object_key)
    except ObjectStoreError as error:
        raise KnowledgeContentUnavailable(
            "Knowledge content is unavailable"
        ) from error

    data = stored.data
    try:
        metadata_valid = (
            stored.metadata.key == object_key
            and stored.metadata.size_bytes == len(data)
        )
    except (AttributeError, TypeError):
        metadata_valid = False
    if (
        not isinstance(data, bytes)
        or not metadata_valid
        or not data
        or len(data) > max_parsed_chars * 4
    ):
        raise KnowledgeContentUnavailable("Knowledge content is unavailable")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise KnowledgeContentUnavailable(
            "Knowledge content is unavailable"
        ) from error
    if not text.strip() or len(text) > max_parsed_chars:
        raise KnowledgeContentUnavailable("Knowledge content is unavailable")
    return text


class KnowledgeDocumentService:
    def __init__(
        self,
        object_store: ObjectStore,
        *,
        vector_store: _KnowledgeCleanupVectorStore | None = None,
        coordinator: DocumentOperationCoordinator | None = None,
        repository: KnowledgeDocumentRepository | None = None,
        collection_service: KnowledgeCollectionService | None = None,
        max_parsed_chars: int = DEFAULT_KNOWLEDGE_MAX_PARSED_CHARS,
    ) -> None:
        if max_parsed_chars <= 0:
            raise ValueError("max_parsed_chars must be positive")
        self.object_store = object_store
        self.vector_store = vector_store
        self.coordinator = coordinator or DocumentOperationCoordinator()
        self.repository = repository or KnowledgeDocumentRepository()
        self.collection_service = collection_service or KnowledgeCollectionService()
        self.max_parsed_chars = max_parsed_chars

    def cleanup_inactive(self, document: InactiveDocumentCleanup) -> None:
        with self.coordinator.hold(document.id):
            self._cleanup_inactive_locked(document)

    def _cleanup_inactive_locked(self, document: InactiveDocumentCleanup) -> None:
        if self.vector_store is None:
            raise RuntimeError("knowledge vector store is required for cleanup")

        errors: list[Exception] = []
        try:
            self.vector_store.delete_document(
                DocumentVectorScope(
                    collection_id=document.collection_id,
                    owner_user_id=document.user_id,
                    document_id=document.id,
                )
            )
        except Exception as error:
            errors.append(error)

        parsed_prefix = (
            f"users/{document.user_id}/knowledge/{document.collection_id}/"
            f"{document.id}/parsed/"
        )
        parsed_keys: list[str] = []
        try:
            for metadata in self.object_store.list(parsed_prefix):
                try:
                    generation_text = metadata.key.removeprefix(parsed_prefix)
                    generation = int(generation_text)
                    canonical = self.parsed_key(
                        document.user_id,
                        document.collection_id,
                        document.id,
                        generation,
                    )
                    if generation < 1 or metadata.key != canonical:
                        raise ValueError
                except (TypeError, ValueError):
                    errors.append(KnowledgeCleanupError("noncanonical parsed key"))
                    continue
                parsed_keys.append(metadata.key)
        except Exception as error:
            errors.append(error)

        canonical_source_key = self.source_key(
            document.user_id,
            document.collection_id,
            document.id,
        )
        for key in [*parsed_keys, canonical_source_key]:
            try:
                # ObjectStore.delete() is explicitly idempotent for missing keys.
                self.object_store.delete(key)
            except Exception as error:
                errors.append(error)

        if errors:
            raise KnowledgeCleanupError(type(errors[0]).__name__)

    @staticmethod
    def source_key(owner_id: int, collection_id: str, document_id: str) -> str:
        return f"users/{owner_id}/knowledge/{collection_id}/{document_id}/source"

    @staticmethod
    def parsed_key(
        owner_id: int,
        collection_id: str,
        document_id: str,
        generation: int,
    ) -> str:
        return (
            f"users/{owner_id}/knowledge/{collection_id}/{document_id}/"
            f"parsed/{generation}"
        )

    def upload_committed(
        self,
        session_factory: sessionmaker[Session],
        *,
        actor_id: int,
        collection_id: str,
        upload: ValidatedUpload,
    ) -> models.KnowledgeDocument:
        document_id = str(uuid4())
        with session_scope(session_factory) as read_session:
            actor = read_session.get(models.User, actor_id)
            if actor is None or not actor.is_active:
                raise not_found("user_not_found", "User not found.")
            collection = self.collection_service.require_manageable(
                read_session,
                actor=actor,
                collection_id=collection_id,
            )
            owner_id = collection.user_id
            validated_collection_id = collection.id

        source_key = self.source_key(
            owner_id,
            validated_collection_id,
            document_id,
        )
        put_attempted = False
        try:
            put_attempted = True
            self.object_store.put(source_key, upload.content, if_none_match=True)
            with session_scope(session_factory) as write_session:
                actor = write_session.get(models.User, actor_id)
                if actor is None or not actor.is_active:
                    raise not_found("user_not_found", "User not found.")
                collection = self.collection_service.require_manageable(
                    write_session,
                    actor=actor,
                    collection_id=validated_collection_id,
                )
                if collection.user_id != owner_id:
                    raise conflict(
                        "resource_changed",
                        "Collection ownership changed.",
                    )
                document = models.KnowledgeDocument(
                    id=document_id,
                    user_id=owner_id,
                    collection_id=collection.id,
                    name=upload.filename,
                    source_object_key=source_key,
                    parsed_object_key=None,
                    mime_type=upload.mime_type,
                    size_bytes=upload.size_bytes,
                    content_hash=upload.content_hash,
                    status="uploaded",
                    index_generation=1,
                    is_active=True,
                )
                write_session.add(document)
                write_session.flush()
                self.repository.queue(write_session, document)
            return document
        except ObjectConflict:
            # A conditional conflict proves the key predated this call.
            raise
        except Exception:
            if put_attempted:
                try:
                    # The key contains this request's new UUID, so deletion is
                    # idempotent even when the PUT outcome was uncertain.
                    self.object_store.delete(source_key)
                except ObjectStoreError:
                    logger.warning(
                        "knowledge_upload_compensation_failed",
                        extra={
                            "document_id": document_id,
                            "exception_type": "ObjectStoreError",
                            "error_code": "knowledge_upload_compensation_failed",
                        },
                        exc_info=False,
                    )
            raise

    def reindex(
        self,
        session: Session,
        *,
        actor: models.User,
        document_id: str,
    ) -> models.KnowledgeDocument:
        document = self._require_manageable(
            session,
            actor=actor,
            document_id=document_id,
            for_update=True,
            require_active=True,
        )
        document.index_generation += 1
        document.parsed_object_key = None
        document.indexed_at = None
        self.repository.queue(session, document)
        return document

    def isolate_for_delete(
        self,
        session: Session,
        *,
        actor: models.User,
        document_id: str,
        cleanup_attempt_id: str,
    ) -> models.KnowledgeDocument:
        document = self._require_manageable(
            session,
            actor=actor,
            document_id=document_id,
            for_update=True,
            require_active=False,
        )
        if document.is_active:
            if self._is_exactly_referenced(session, document.id):
                raise conflict(
                    "resource_in_use",
                    "Document is referenced by an active Agent.",
                )
        self.repository.begin_cleanup(
            session,
            document,
            cleanup_attempt_id=cleanup_attempt_id,
        )
        return document

    def require_in_collection(
        self,
        document_id: str,
        collection_id: str,
        session: Session,
    ) -> models.KnowledgeDocument:
        document = self.repository.get(session, document_id=document_id)
        if document is None or document.collection_id != collection_id:
            raise not_found(
                "knowledge_document_not_found",
                "Document not found.",
            )
        return document

    def require_visible(
        self,
        session: Session,
        *,
        user_id: int,
        collection_id: str,
        document_id: str,
    ) -> models.KnowledgeDocument:
        collection = self.collection_service.require_visible(
            session,
            user_id=user_id,
            collection_id=collection_id,
        )
        document = self.require_in_collection(
            document_id,
            collection_id,
            session,
        )
        if not document.is_active or document.user_id != collection.user_id:
            raise not_found(
                "knowledge_document_not_found",
                "Document not found.",
            )
        return document

    def read_parsed(self, document: models.KnowledgeDocument) -> str:
        if document.status != "ready":
            raise conflict(
                "knowledge_document_not_ready",
                "Document has not completed indexing.",
            )
        if not document.parsed_object_key:
            raise service_unavailable(
                "knowledge_unavailable",
                "Knowledge content is unavailable.",
            )
        expected_key = self.parsed_key(
            document.user_id,
            document.collection_id,
            document.id,
            document.index_generation,
        )
        if document.parsed_object_key != expected_key:
            raise service_unavailable(
                "knowledge_unavailable",
                "Knowledge content is unavailable.",
            )
        try:
            return read_parsed_text(
                self.object_store,
                object_key=expected_key,
                max_parsed_chars=self.max_parsed_chars,
            )
        except KnowledgeContentUnavailable as error:
            raise service_unavailable(
                "knowledge_unavailable",
                "Knowledge content is unavailable.",
            ) from error

    def read_source(self, document: models.KnowledgeDocument) -> StoredObject:
        expected_key = self.source_key(
            document.user_id,
            document.collection_id,
            document.id,
        )
        if document.source_object_key != expected_key:
            raise service_unavailable(
                "knowledge_unavailable",
                "Knowledge content is unavailable.",
            )
        try:
            stored = self.object_store.get(expected_key)
        except ObjectStoreError as error:
            raise service_unavailable(
                "knowledge_unavailable",
                "Knowledge content is unavailable.",
            ) from error
        try:
            integrity_valid = (
                stored.metadata.key == expected_key
                and stored.metadata.size_bytes == document.size_bytes
                and len(stored.data) == document.size_bytes
                and hashlib.sha256(stored.data).hexdigest()
                == document.content_hash
            )
        except (AttributeError, TypeError):
            integrity_valid = False
        if not integrity_valid:
            raise service_unavailable(
                "knowledge_unavailable",
                "Knowledge content is unavailable.",
            )
        return stored

    def _require_manageable(
        self,
        session: Session,
        *,
        actor: models.User,
        document_id: str,
        for_update: bool = False,
        require_active: bool = True,
    ) -> models.KnowledgeDocument:
        snapshot = self.repository.get(session, document_id=document_id)
        if snapshot is None:
            raise not_found(
                "knowledge_document_not_found",
                "Document not found.",
            )
        collection = self.collection_service.require_manageable(
            session,
            actor=actor,
            collection_id=snapshot.collection_id,
        )
        document = (
            self.repository.get_for_update(session, document_id=document_id)
            if for_update
            else snapshot
        )
        if (
            document is None
            or document.collection_id != snapshot.collection_id
            or document.user_id != collection.user_id
        ):
            raise not_found(
                "knowledge_document_not_found",
                "Document not found.",
            )
        if require_active and not document.is_active:
            raise not_found(
                "knowledge_document_not_found",
                "Document not found.",
            )
        return document

    @staticmethod
    def _is_exactly_referenced(session: Session, document_id: str) -> bool:
        for resource in ResourceRepository().list_active_agents(session):
            config = AgentConfig.model_validate(resource.config_json)
            for scope in config.knowledge_scopes:
                if (
                    scope.document_ids is not None
                    and document_id in scope.document_ids
                ):
                    return True
        return False
