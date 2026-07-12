from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import logging
from threading import Event, Lock, Thread
from typing import Callable, Literal, Protocol

from sqlalchemy.orm import Session, sessionmaker

from app.core.limits import DEFAULT_KNOWLEDGE_WORKER_POLL_INTERVAL_SECONDS
from app.db.session import session_scope
from app.repositories.knowledge_document_repository import (
    ClaimedDocument,
    KnowledgeDocumentRepository,
)
from app.repositories.vector_store import VectorStoreUnavailable
from app.schemas.knowledge_collections import KnowledgeCollectionConfig
from app.services.document_operation_coordinator import DocumentOperationCoordinator
from app.services.extraction_service import ExtractionError, ExtractionService
from app.services.knowledge_chunk_service import KnowledgeChunkService
from app.services.knowledge_document_service import KnowledgeDocumentService
from app.services.knowledge_vector_store import DocumentGeneration
from app.storage.object_store import ObjectConflict, ObjectStore, ObjectStoreError


logger = logging.getLogger(__name__)


class KnowledgeParseError(RuntimeError):
    pass


class KnowledgeWorkerStopTimeout(RuntimeError):
    pass


class _KnowledgeWorkerVectorStore(Protocol):
    def upsert_generation(self, chunks) -> None: ...

    def delete_generation(self, scope: DocumentGeneration) -> None: ...

    def delete_generations_before(self, current: DocumentGeneration) -> None: ...


def map_index_error(error: Exception) -> str:
    if isinstance(error, (KnowledgeParseError, ExtractionError)):
        return "knowledge_parse_failed"
    if isinstance(error, VectorStoreUnavailable):
        return "knowledge_unavailable"
    if isinstance(error, ObjectStoreError):
        return "knowledge_storage_unavailable"
    return "knowledge_index_failed"


def safe_error_message(error: Exception) -> str:
    return {
        "knowledge_parse_failed": "Document extraction failed.",
        "knowledge_unavailable": "Knowledge vector service is unavailable.",
        "knowledge_storage_unavailable": "Knowledge object storage is unavailable.",
        "knowledge_index_failed": "Knowledge indexing failed.",
    }[map_index_error(error)]


class KnowledgeIndexWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        repository: KnowledgeDocumentRepository,
        object_store: ObjectStore,
        extraction: ExtractionService,
        vector_store: _KnowledgeWorkerVectorStore,
        coordinator: DocumentOperationCoordinator,
        clock: Callable[[], datetime],
        processing_timeout: timedelta,
        poll_interval: float = DEFAULT_KNOWLEDGE_WORKER_POLL_INTERVAL_SECONDS,
    ) -> None:
        if processing_timeout <= timedelta(0):
            raise ValueError("processing_timeout must be positive")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self.session_factory = session_factory
        self.repository = repository
        self.object_store = object_store
        self.extraction = extraction
        self.vector_store = vector_store
        self.coordinator = coordinator
        self.clock = clock
        self.processing_timeout = processing_timeout
        self.poll_interval = poll_interval
        self._stop = Event()
        self._thread_lock = Lock()
        self._thread: Thread | None = None

    @property
    def is_alive(self) -> bool:
        thread = self._thread
        return bool(thread is not None and thread.is_alive())

    def start(self) -> None:
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = Thread(
                target=self._run,
                name="knowledge-index-worker",
                daemon=True,
            )
            self._thread = thread
            try:
                thread.start()
            except Exception:
                self._thread = None
                raise

    def stop(self, *, timeout: float | None) -> None:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must not be negative")
        self._stop.set()
        thread = self._thread
        if thread is None:
            return
        thread.join(timeout=timeout)
        if thread.is_alive():
            raise KnowledgeWorkerStopTimeout(
                "Knowledge worker did not stop before the timeout"
            )
        with self._thread_lock:
            if self._thread is thread:
                self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception as error:
                worked = False
                logger.warning(
                    "knowledge_worker_iteration_failed",
                    extra={
                        "exception_type": type(error).__name__,
                        "error_code": "knowledge_worker_iteration_failed",
                    },
                    exc_info=False,
                )
            if not worked:
                self._stop.wait(self.poll_interval)

    def run_once(self) -> bool:
        with session_scope(self.session_factory) as session:
            item = self.repository.claim_next(
                session,
                stale_before=self.clock() - self.processing_timeout,
            )
        if item is None:
            return False

        parsed_key = KnowledgeDocumentService.parsed_key(
            item.owner_user_id,
            item.collection_id,
            item.id,
            item.generation,
        )
        try:
            source = self._read_verified_source(item)
            parsed = self.extraction.extract_required(
                item.filename,
                item.mime_type,
                source,
            )
            if parsed.kind != "text" or parsed.text is None or not parsed.text.strip():
                raise KnowledgeParseError(
                    "Document did not contain extractable text"
                )
            collection_config = KnowledgeCollectionConfig.model_validate(
                item.config_json
            )
            chunks = KnowledgeChunkService.from_config(collection_config).split(
                document_id=item.id,
                collection_id=item.collection_id,
                owner_user_id=item.owner_user_id,
                generation=item.generation,
                content_hash=item.content_hash,
                filename=item.filename,
                text=parsed.text,
            )
            if not chunks:
                raise KnowledgeParseError("Document did not produce source chunks")

            with self.coordinator.hold(item.id):
                if not self._prepare_for_mutation(item, parsed_key):
                    return True
                self._put_parsed_idempotently(
                    parsed_key,
                    parsed.text.encode("utf-8"),
                )

                generation = self._vector_generation(item)
                if not self._prepare_for_mutation(item, parsed_key):
                    return True
                # A crashed attempt may have partially populated this unpublished
                # generation. Rebuild it from a clean exact-generation projection.
                self.vector_store.delete_generation(generation)
                if not self._prepare_for_mutation(item, parsed_key):
                    return True
                self.vector_store.upsert_generation(chunks)

                with session_scope(self.session_factory) as session:
                    published = self.repository.complete_generation(
                        session,
                        document_id=item.id,
                        generation=item.generation,
                        processing_attempt_id=item.processing_attempt_id,
                        parsed_object_key=parsed_key,
                    )
                if published:
                    self._cleanup_published_old_generations(item, parsed_key)
                elif self._claim_disposition(item) == "obsolete":
                    self._cleanup_stale_attempt(item, parsed_key)
            return True
        except Exception as error:
            with self.coordinator.hold(item.id):
                index_error_code = map_index_error(error)
                logger.warning(
                    "knowledge_generation_failed",
                    extra={
                        "document_id": item.id,
                        "index_generation": item.generation,
                        "exception_type": type(error).__name__,
                        "error_code": index_error_code,
                    },
                    exc_info=False,
                )
                marked_failed = False
                try:
                    with session_scope(self.session_factory) as session:
                        marked_failed = self.repository.fail_generation(
                            session,
                            document_id=item.id,
                            generation=item.generation,
                            processing_attempt_id=item.processing_attempt_id,
                            error_code=index_error_code,
                            error_message=safe_error_message(error),
                        )
                except Exception as state_error:
                    logger.warning(
                        "knowledge_generation_failure_state_unavailable",
                        extra={
                            "document_id": item.id,
                            "index_generation": item.generation,
                            "exception_type": type(state_error).__name__,
                            "error_code": "knowledge_failure_state_unavailable",
                        },
                        exc_info=False,
                    )
                    raise

                if marked_failed or self._claim_disposition(item) == "obsolete":
                    self._cleanup_stale_attempt(item, parsed_key)
            return True

    def _read_verified_source(self, item: ClaimedDocument) -> bytes:
        canonical_key = KnowledgeDocumentService.source_key(
            item.owner_user_id,
            item.collection_id,
            item.id,
        )
        if item.source_object_key != canonical_key:
            raise KnowledgeParseError("Knowledge source key is invalid")
        stored = self.object_store.get(canonical_key)
        data = stored.data
        try:
            valid = (
                isinstance(data, bytes)
                and stored.metadata.key == canonical_key
                and stored.metadata.size_bytes == item.size_bytes
                and len(data) == item.size_bytes
                and hashlib.sha256(data).hexdigest() == item.content_hash
            )
        except (AttributeError, TypeError):
            valid = False
        if not valid:
            raise KnowledgeParseError("Knowledge source integrity check failed")
        return data

    def _put_parsed_idempotently(self, key: str, content: bytes) -> None:
        try:
            self.object_store.put(key, content, if_none_match=True)
        except ObjectConflict as error:
            existing = self.object_store.get(key)
            try:
                matches = (
                    isinstance(existing.data, bytes)
                    and existing.metadata.key == key
                    and existing.metadata.size_bytes == len(existing.data)
                    and existing.data == content
                )
            except (AttributeError, TypeError):
                matches = False
            if not matches:
                raise KnowledgeParseError(
                    "Parsed output conflicts with the current generation"
                ) from error

    def _prepare_for_mutation(
        self,
        item: ClaimedDocument,
        parsed_key: str,
    ) -> bool:
        disposition = self._claim_disposition(item)
        if disposition == "owned":
            return True
        if disposition == "obsolete":
            self._cleanup_stale_attempt(item, parsed_key)
        # A same-generation newer claim/ready row owns the shared parsed key
        # and exact vector projection. A late attempt must leave both untouched.
        return False

    def _claim_disposition(
        self,
        item: ClaimedDocument,
    ) -> Literal["owned", "superseded", "obsolete"]:
        with self.session_factory() as session:
            state = self.repository.get_attempt_state(
                session,
                document_id=item.id,
            )
        if state is None:
            return "obsolete"
        if not state.is_active or state.generation != item.generation:
            return "obsolete"
        if (
            state.status == "processing"
            and state.processing_attempt_id == item.processing_attempt_id
        ):
            return "owned"
        return "superseded"

    def _cleanup_published_old_generations(
        self,
        item: ClaimedDocument,
        current_key: str,
    ) -> None:
        with self.session_factory() as session:
            state = self.repository.get_attempt_state(
                session,
                document_id=item.id,
            )
        if (
            state is None
            or not state.is_active
            or state.status != "ready"
            or state.generation != item.generation
            or state.parsed_object_key != current_key
        ):
            return

        errors: list[Exception] = []
        parsed_prefix = (
            f"users/{item.owner_user_id}/knowledge/{item.collection_id}/"
            f"{item.id}/parsed/"
        )
        try:
            metadata_items = self.object_store.list(parsed_prefix)
        except Exception as error:
            errors.append(error)
            metadata_items = []
        for metadata in metadata_items:
            try:
                generation = int(metadata.key.removeprefix(parsed_prefix))
                canonical = KnowledgeDocumentService.parsed_key(
                    item.owner_user_id,
                    item.collection_id,
                    item.id,
                    generation,
                )
                if generation < 1 or metadata.key != canonical:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(KnowledgeParseError("noncanonical parsed key"))
                continue
            if generation >= item.generation:
                continue
            try:
                self.object_store.delete(metadata.key)
            except Exception as error:
                errors.append(error)
        # The adapter performs one tenant-scoped `generation < current`
        # mutation. A future generation remains outside this filter even if it
        # is written immediately after the DB check above.
        try:
            self.vector_store.delete_generations_before(
                self._vector_generation(item)
            )
        except Exception as error:
            errors.append(error)
        if errors:
            logger.warning(
                "knowledge_generation_cleanup_failed",
                extra={
                    "document_id": item.id,
                    "index_generation": item.generation,
                    "exception_type": type(errors[0]).__name__,
                    "error_code": "knowledge_cleanup_failed",
                },
                exc_info=False,
            )

    def _cleanup_stale_attempt(
        self,
        item: ClaimedDocument,
        parsed_key: str,
    ) -> None:
        errors: list[Exception] = []
        try:
            self.object_store.delete(parsed_key)
        except Exception as error:
            errors.append(error)
        try:
            self.vector_store.delete_generation(self._vector_generation(item))
        except Exception as error:
            errors.append(error)
        if not errors:
            return
        logger.warning(
            "knowledge_generation_cleanup_failed",
            extra={
                "document_id": item.id,
                "index_generation": item.generation,
                "exception_type": type(errors[0]).__name__,
                "error_code": "knowledge_stale_cleanup_failed",
            },
            exc_info=False,
        )

    @staticmethod
    def _vector_generation(item: ClaimedDocument) -> DocumentGeneration:
        return DocumentGeneration(
            collection_id=item.collection_id,
            owner_user_id=item.owner_user_id,
            document_id=item.id,
            index_generation=item.generation,
            content_hash=item.content_hash,
        )
