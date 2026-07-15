from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
from threading import Event, Thread

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.session import session_scope
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.services.extraction_service import ExtractionService
from app.services.document_operation_coordinator import DocumentOperationCoordinator
from app.services.knowledge_document_service import (
    InactiveDocumentCleanup,
    KnowledgeDocumentService,
)
from app.services.knowledge_index_worker import (
    KnowledgeIndexWorker,
    KnowledgeWorkerStopTimeout,
    map_index_error,
    safe_error_message,
)
from app.services.embedding_service import EmbeddingProviderError
from app.storage import ObjectMetadata, ObjectStoreUnavailable, StoredObject
from tests.fake_object_store import FakeObjectStore
from tests.fakes import FakeKnowledgeVectorStore


NOW = datetime(2026, 7, 14, 8, 0, tzinfo=UTC)


def test_embedding_provider_failures_keep_safe_specific_index_error() -> None:
    error = EmbeddingProviderError(
        "embedding_invalid_request",
        "provider details are intentionally hidden",
    )

    assert map_index_error(error) == "embedding_invalid_request"
    assert safe_error_message(error) == "Embedding provider rejected the request."


@pytest.fixture
def session_factory(tmp_path) -> sessionmaker[Session]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'knowledge-worker.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    return sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def _queued_document(
    factory: sessionmaker[Session],
    *,
    source: bytes = b"# Source\nExact text",
    collection_active: bool = True,
) -> models.KnowledgeDocument:
    with factory() as session:
        collection = models.Resource(
            id="collection-1",
            user_id=7,
            resource_type="knowledge_collection",
            name="资料库",
            config_json={},
            is_active=collection_active,
            deleted_at=None,
        )
        document = models.KnowledgeDocument(
            id="document-1",
            user_id=7,
            collection_id=collection.id,
            name="source.md",
            source_object_key="users/7/knowledge/collection-1/document-1/source",
            parsed_object_key=None,
            mime_type="text/markdown",
            size_bytes=len(source),
            content_hash=hashlib.sha256(source).hexdigest(),
            status="queued",
            index_generation=1,
            is_active=True,
            updated_at=NOW - timedelta(minutes=10),
        )
        session.add_all([collection, document])
        session.commit()
        return document


def _worker(
    factory: sessionmaker[Session],
    store: FakeObjectStore,
    vectors: FakeKnowledgeVectorStore,
    *,
    extraction: ExtractionService | None = None,
    clock=None,
    coordinator: DocumentOperationCoordinator | None = None,
) -> KnowledgeIndexWorker:
    return KnowledgeIndexWorker(
        session_factory=factory,
        repository=KnowledgeDocumentRepository(),
        object_store=store,
        extraction=extraction
        or ExtractionService(
                max_parsed_chars=10_000,
                max_decompressed_bytes=10_000,
                max_pdf_pages=10,
            ),
        vector_store=vectors,
        coordinator=coordinator or DocumentOperationCoordinator(),
        clock=clock or (lambda: NOW),
        processing_timeout=timedelta(minutes=5),
        poll_interval=0.01,
    )


def test_worker_writes_generation_specific_parsed_object_and_marks_ready(
    session_factory,
) -> None:
    source = b"# Source\nExact text"
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()

    assert _worker(session_factory, store, vectors).run_once() is True

    parsed_key = KnowledgeDocumentService.parsed_key(7, "collection-1", "document-1", 1)
    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "ready"
        assert current.parsed_object_key == parsed_key
        assert current.index_generation == 1
        assert current.error_code is None
    assert store.get(parsed_key).data == source
    assert vectors.has_generation("document-1", 1)


def test_worker_rejects_source_that_no_longer_matches_mysql_hash(
    session_factory,
) -> None:
    document = _queued_document(session_factory, source=b"original")
    store = FakeObjectStore()
    store.put(document.source_object_key, b"tampered", if_none_match=True)
    vectors = FakeKnowledgeVectorStore()

    assert _worker(session_factory, store, vectors).run_once() is True

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "failed"
        assert current.error_code == "knowledge_parse_failed"
        assert current.error_message == "Document extraction failed."
    assert not vectors.has_generation("document-1", 1)
    assert store.keys() == [document.source_object_key]


@pytest.mark.parametrize(
    "corruption",
    ["metadata_key", "metadata_size", "actual_length"],
)
def test_worker_rejects_each_source_integrity_boundary(
    session_factory,
    monkeypatch,
    corruption: str,
) -> None:
    source = b"original"
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    original_get = store.get

    def corrupt_get(key: str) -> StoredObject:
        stored = original_get(key)
        if key != document.source_object_key:
            return stored
        metadata_key = stored.metadata.key
        metadata_size = stored.metadata.size_bytes
        data = stored.data
        if corruption == "metadata_key":
            metadata_key = f"{metadata_key}-polluted"
        elif corruption == "metadata_size":
            metadata_size += 1
        else:
            data += b"x"
        return StoredObject(
            data=data,
            metadata=ObjectMetadata(
                key=metadata_key,
                etag=stored.metadata.etag,
                size_bytes=metadata_size,
            ),
        )

    monkeypatch.setattr(store, "get", corrupt_get)
    vectors = FakeKnowledgeVectorStore()

    assert _worker(session_factory, store, vectors).run_once() is True

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "failed"
        assert current.error_code == "knowledge_parse_failed"
    assert vectors.upsert_calls == []


def test_retry_reuses_identical_generation_parsed_object_after_crash(
    session_factory,
) -> None:
    source = b"# Source\nExact text"
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    parsed_key = KnowledgeDocumentService.parsed_key(7, "collection-1", "document-1", 1)
    store.put(parsed_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()

    assert _worker(session_factory, store, vectors).run_once() is True

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "ready"
        assert current.parsed_object_key == parsed_key


@pytest.mark.parametrize(
    "conflict",
    ["different_content", "metadata_key", "metadata_size"],
)
def test_retry_rejects_conflicting_or_untrusted_parsed_object(
    session_factory,
    monkeypatch,
    conflict: str,
) -> None:
    source = b"# Source\nExact text"
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    parsed_key = KnowledgeDocumentService.parsed_key(
        7,
        "collection-1",
        "document-1",
        1,
    )
    store.put(
        parsed_key,
        b"different" if conflict == "different_content" else source,
        if_none_match=True,
    )
    original_get = store.get

    def parsed_get(key: str) -> StoredObject:
        stored = original_get(key)
        if key != parsed_key or conflict == "different_content":
            return stored
        return StoredObject(
            data=stored.data,
            metadata=ObjectMetadata(
                key=(
                    f"{parsed_key}-polluted"
                    if conflict == "metadata_key"
                    else parsed_key
                ),
                etag=stored.metadata.etag,
                size_bytes=(
                    stored.metadata.size_bytes + 1
                    if conflict == "metadata_size"
                    else stored.metadata.size_bytes
                ),
            ),
        )

    monkeypatch.setattr(store, "get", parsed_get)
    vectors = FakeKnowledgeVectorStore()

    assert _worker(session_factory, store, vectors).run_once() is True

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "failed"
        assert current.error_code == "knowledge_parse_failed"
        assert current.parsed_object_key is None
    assert parsed_key not in store.keys()
    assert vectors.upsert_calls == []


def test_partial_vector_upsert_failure_cleans_exact_attempt(
    session_factory,
) -> None:
    source = b"# Source\nExact text"
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()
    vectors.fail_after_partial_upsert()

    assert _worker(session_factory, store, vectors).run_once() is True

    parsed_key = KnowledgeDocumentService.parsed_key(7, "collection-1", "document-1", 1)
    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "failed"
        assert current.error_code == "knowledge_unavailable"
    assert parsed_key not in store.keys()
    assert not vectors.has_generation("document-1", 1)


def test_worker_does_not_claim_document_from_inactive_collection(
    session_factory,
) -> None:
    source = b"source"
    document = _queued_document(
        session_factory,
        source=source,
        collection_active=False,
    )
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)

    assert _worker(
        session_factory,
        store,
        FakeKnowledgeVectorStore(),
    ).run_once() is False

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "queued"


def test_late_same_generation_claim_never_mutates_newer_projection(
    session_factory,
) -> None:
    source = ("section content " * 80).encode()
    document = _queued_document(session_factory, source=source)
    with session_factory() as session:
        collection = session.get(models.Resource, document.collection_id)
        assert collection is not None
        collection.config_json = {"chunk_size": 400, "chunk_overlap": 0}
        session.commit()

    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()
    extraction_started = Event()
    release_old_claim = Event()

    class BlockingExtraction(ExtractionService):
        def extract_required(self, filename, mime_type, content):
            extraction_started.set()
            assert release_old_claim.wait(timeout=10)
            return super().extract_required(filename, mime_type, content)

    old_worker = _worker(
        session_factory,
        store,
        vectors,
        extraction=BlockingExtraction(
            max_parsed_chars=10_000,
            max_decompressed_bytes=10_000,
            max_pdf_pages=10,
        ),
        clock=models._now,
    )
    old_result: list[bool] = []
    thread = Thread(target=lambda: old_result.append(old_worker.run_once()))
    thread.start()
    assert extraction_started.wait(timeout=10)

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        collection = session.get(models.Resource, document.collection_id)
        assert current is not None and collection is not None
        current.updated_at = models._now() - timedelta(minutes=10)
        collection.config_json = {"chunk_size": 200, "chunk_overlap": 0}
        session.commit()

    new_worker = _worker(
        session_factory,
        store,
        vectors,
        clock=models._now,
    )
    assert new_worker.run_once() is True
    assert len(vectors.upsert_calls) == 1
    assert all(len(chunk.content) <= 200 for chunk in vectors.upsert_calls[0])
    current_projection = list(vectors.upsert_calls[0])
    delete_calls = list(vectors.delete_generation_calls)

    release_old_claim.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert old_result == [True]

    assert vectors.upsert_calls == [current_projection]
    assert vectors.delete_generation_calls == delete_calls
    assert vectors.has_generation(document.id, 1)
    parsed_key = KnowledgeDocumentService.parsed_key(7, "collection-1", document.id, 1)
    assert store.put_calls.count(parsed_key) == 1


def test_claim_identity_rejects_late_same_generation_completion(
    session_factory,
) -> None:
    _queued_document(session_factory)
    repository = KnowledgeDocumentRepository()
    with session_scope(session_factory) as session:
        claim = repository.claim_next(
            session,
            stale_before=models._now(),
        )
    assert claim is not None

    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, claim.id)
        assert current is not None
        current.status = "processing"
        current.processing_attempt_id = "newer-processing-attempt"

    with session_scope(session_factory) as session:
        assert repository.complete_generation(
            session,
            document_id=claim.id,
            generation=claim.generation,
            processing_attempt_id=claim.processing_attempt_id,
            parsed_object_key="parsed/late",
        ) is False

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, claim.id)
        assert current is not None
        assert current.status == "processing"
        assert current.parsed_object_key is None


def test_cleanup_attempt_identity_rejects_stale_success_and_failure(
    session_factory,
) -> None:
    document = _queued_document(session_factory)
    repository = KnowledgeDocumentRepository()

    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        repository.begin_cleanup(
            session,
            current,
            cleanup_attempt_id="older-cleanup-attempt",
        )
    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        repository.begin_cleanup(
            session,
            current,
            cleanup_attempt_id="current-cleanup-attempt",
        )

    with session_scope(session_factory) as session:
        assert repository.mark_cleanup_failed_if_inactive(
            session,
            document_id=document.id,
            cleanup_attempt_id="older-cleanup-attempt",
            message="stale failure",
        ) is False
    with session_scope(session_factory) as session:
        assert repository.clear_cleanup_error_if_inactive(
            session,
            document_id=document.id,
            cleanup_attempt_id="current-cleanup-attempt",
        ) is True
    with session_scope(session_factory) as session:
        assert repository.mark_cleanup_failed_if_inactive(
            session,
            document_id=document.id,
            cleanup_attempt_id="older-cleanup-attempt",
            message="late stale failure",
        ) is False

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.is_active is False
        assert current.cleanup_attempt_id is None
        assert current.error_code is None

    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        repository.begin_cleanup(
            session,
            current,
            cleanup_attempt_id="older-retry-attempt",
        )
    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        repository.begin_cleanup(
            session,
            current,
            cleanup_attempt_id="current-retry-attempt",
        )
    with session_scope(session_factory) as session:
        assert repository.mark_cleanup_failed_if_inactive(
            session,
            document_id=document.id,
            cleanup_attempt_id="current-retry-attempt",
            message="current failure",
        ) is True
    with session_scope(session_factory) as session:
        assert repository.clear_cleanup_error_if_inactive(
            session,
            document_id=document.id,
            cleanup_attempt_id="older-retry-attempt",
        ) is False
    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.cleanup_attempt_id is None
        assert current.error_code == "knowledge_cleanup_failed"


def test_delete_fence_wins_before_worker_projection_mutation(
    session_factory,
) -> None:
    source = b"# Source\nExact text"
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()
    coordinator = DocumentOperationCoordinator()
    extraction_started = Event()
    release_extraction = Event()

    class BlockingExtraction(ExtractionService):
        def extract_required(self, filename, mime_type, content):
            extraction_started.set()
            assert release_extraction.wait(timeout=10)
            return super().extract_required(filename, mime_type, content)

    worker = _worker(
        session_factory,
        store,
        vectors,
        extraction=BlockingExtraction(
            max_parsed_chars=10_000,
            max_decompressed_bytes=10_000,
            max_pdf_pages=10,
        ),
        coordinator=coordinator,
    )
    result: list[bool] = []
    thread = Thread(target=lambda: result.append(worker.run_once()))
    thread.start()
    assert extraction_started.wait(timeout=10)

    service = KnowledgeDocumentService(
        store,
        vector_store=vectors,
        coordinator=coordinator,
    )
    with coordinator.hold(document.id):
        with session_scope(session_factory) as session:
            current = session.get(models.KnowledgeDocument, document.id)
            assert current is not None
            service.repository.begin_cleanup(
                session,
                current,
                cleanup_attempt_id="delete-wins-attempt",
            )
        service.cleanup_inactive(
            InactiveDocumentCleanup(
                id=document.id,
                user_id=document.user_id,
                collection_id=document.collection_id,
            )
        )
        with session_scope(session_factory) as session:
            assert service.repository.clear_cleanup_error_if_inactive(
                session,
                document_id=document.id,
                cleanup_attempt_id="delete-wins-attempt",
            ) is True

    release_extraction.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert result == [True]
    assert vectors.upsert_calls == []
    assert store.put_calls == [document.source_object_key]
    assert store.keys() == []
    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.is_active is False
        assert current.error_code is None
        assert current.parsed_object_key is None


def test_worker_rejects_noncanonical_source_pointer_before_object_read(
    session_factory,
) -> None:
    source = b"source"
    document = _queued_document(session_factory, source=source)
    canonical_key = document.source_object_key
    polluted_key = f"{canonical_key}-polluted"
    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        current.source_object_key = polluted_key
    store = FakeObjectStore()
    store.put(canonical_key, source, if_none_match=True)
    store.put(polluted_key, source, if_none_match=True)

    assert _worker(
        session_factory,
        store,
        FakeKnowledgeVectorStore(),
    ).run_once() is True

    assert store.get_calls == []
    assert store.keys() == sorted([canonical_key, polluted_key])
    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "failed"
        assert current.error_code == "knowledge_parse_failed"


def test_old_publisher_cleanup_cannot_delete_new_generation(
    session_factory,
) -> None:
    source = ("source content " * 80).encode()
    document = _queued_document(session_factory, source=source)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()
    cleanup_started = Event()
    release_cleanup = Event()

    class PausingCleanupWorker(KnowledgeIndexWorker):
        def _cleanup_published_old_generations(self, item, current_key):
            cleanup_started.set()
            assert release_cleanup.wait(timeout=10)
            return super()._cleanup_published_old_generations(item, current_key)

    first = PausingCleanupWorker(
        session_factory=session_factory,
        repository=KnowledgeDocumentRepository(),
        object_store=store,
        extraction=ExtractionService(
            max_parsed_chars=10_000,
            max_decompressed_bytes=10_000,
            max_pdf_pages=10,
        ),
        vector_store=vectors,
        coordinator=DocumentOperationCoordinator(),
        clock=models._now,
        processing_timeout=timedelta(minutes=5),
        poll_interval=0.01,
    )
    first_result: list[bool] = []
    thread = Thread(target=lambda: first_result.append(first.run_once()))
    thread.start()
    assert cleanup_started.wait(timeout=10)

    with session_scope(session_factory) as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        current.index_generation = 2
        current.status = "queued"
        current.parsed_object_key = None
        current.indexed_at = None
        current.updated_at = models._now()

    assert _worker(
        session_factory,
        store,
        vectors,
        clock=models._now,
    ).run_once() is True
    assert vectors.has_generation(document.id, 2)
    current_delete_calls = list(vectors.delete_generation_calls)
    generation_two_key = KnowledgeDocumentService.parsed_key(
        7,
        "collection-1",
        document.id,
        2,
    )
    assert generation_two_key in store.keys()

    release_cleanup.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert first_result == [True]
    assert vectors.has_generation(document.id, 2)
    assert vectors.delete_generation_calls == current_delete_calls
    assert generation_two_key in store.keys()


def test_worker_errors_and_logs_never_expose_source_or_object_key(
    session_factory,
    caplog,
) -> None:
    secret = "PRIVATE-SOURCE-CONTENT"
    document = _queued_document(session_factory)
    store = FakeObjectStore(
        get_error=ObjectStoreUnavailable(
            f"{document.source_object_key}:{secret}"
        )
    )

    with caplog.at_level("WARNING"):
        assert _worker(
            session_factory,
            store,
            FakeKnowledgeVectorStore(),
        ).run_once() is True

    with session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.error_code == "knowledge_storage_unavailable"
        assert secret not in (current.error_message or "")
        assert document.source_object_key not in (current.error_message or "")
    assert secret not in caplog.text
    assert document.source_object_key not in caplog.text


def test_stop_timeout_never_reports_worker_stopped_while_attempt_is_running(
    session_factory,
) -> None:
    started = Event()
    release = Event()

    class BlockingWorker(KnowledgeIndexWorker):
        def run_once(self):
            started.set()
            assert release.wait(timeout=10)
            return False

    worker = BlockingWorker(
        session_factory=session_factory,
        repository=KnowledgeDocumentRepository(),
        object_store=FakeObjectStore(),
        extraction=ExtractionService(),
        vector_store=FakeKnowledgeVectorStore(),
        coordinator=DocumentOperationCoordinator(),
        clock=models._now,
        processing_timeout=timedelta(minutes=5),
        poll_interval=0.01,
    )
    worker.start()
    assert started.wait(timeout=10)

    with pytest.raises(KnowledgeWorkerStopTimeout):
        worker.stop(timeout=0.001)
    assert worker.is_alive is True

    release.set()
    worker.stop(timeout=None)
    assert worker.is_alive is False


def test_worker_loop_continues_after_transient_claim_failure(
    session_factory,
) -> None:
    retried = Event()

    class FlakyRepository(KnowledgeDocumentRepository):
        def __init__(self) -> None:
            self.calls = 0

        def claim_next(self, session, *, stale_before):
            del session, stale_before
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient claim failure")
            retried.set()
            return None

    repository = FlakyRepository()
    worker = KnowledgeIndexWorker(
        session_factory=session_factory,
        repository=repository,
        object_store=FakeObjectStore(),
        extraction=ExtractionService(),
        vector_store=FakeKnowledgeVectorStore(),
        coordinator=DocumentOperationCoordinator(),
        clock=models._now,
        processing_timeout=timedelta(minutes=5),
        poll_interval=0.01,
    )

    worker.start()
    assert retried.wait(timeout=10)
    worker.stop(timeout=10)

    assert repository.calls >= 2
    assert worker.is_alive is False


def test_worker_start_is_idempotent(session_factory) -> None:
    worker = _worker(
        session_factory,
        FakeObjectStore(),
        FakeKnowledgeVectorStore(),
    )

    worker.start()
    first_thread = worker._thread
    worker.start()

    assert worker._thread is first_thread
    worker.stop(timeout=10)


def test_worker_start_failure_does_not_retain_dead_thread(
    session_factory,
    monkeypatch,
) -> None:
    class FailingThread:
        def __init__(self, **_kwargs) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread start failed")

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(
        "app.services.knowledge_index_worker.Thread",
        FailingThread,
    )
    worker = _worker(
        session_factory,
        FakeObjectStore(),
        FakeKnowledgeVectorStore(),
    )

    with pytest.raises(RuntimeError, match="thread start failed"):
        worker.start()

    assert worker._thread is None
    assert worker.is_alive is False
