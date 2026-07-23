from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import os
from threading import Event

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.config import Settings
from app.db import models
from app.db.session import (
    create_engine_for_settings,
    make_session_factory,
    session_scope,
)
from app.repositories.knowledge_document_repository import KnowledgeDocumentRepository
from app.services.extraction_service import ExtractionService
from app.services.document_operation_coordinator import DocumentOperationCoordinator
from app.services.knowledge_index_worker import KnowledgeIndexWorker
from tests.fake_object_store import FakeObjectStore
from tests.fakes import FakeKnowledgeVectorStore


def _disposable_url() -> str:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    value = os.environ.get("MYSQL_RESOURCE_RACE_DATABASE_URL")
    if not value:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires MYSQL_RESOURCE_RACE_DATABASE_URL"
        )
    url = make_url(value)
    default = make_url(Settings(_env_file=None).database_url)
    if (
        not url.drivername.startswith("mysql")
        or not url.database
        or url.database.casefold()
        in {"mysql", "sys", "information_schema", "performance_schema"}
        or not url.database.casefold().endswith("_test")
        or url.database.casefold() == (default.database or "").casefold()
    ):
        pytest.fail("Knowledge Worker integration requires a disposable MySQL database")
    return url.render_as_string(hide_password=False)


def test_knowledge_worker_guard_rejects_non_test_schema(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.setenv(
        "MYSQL_RESOURCE_RACE_DATABASE_URL",
        "mysql+pymysql://user:pass@127.0.0.1/production",
    )

    with pytest.raises(pytest.fail.Exception, match="disposable"):
        _disposable_url()


@pytest.fixture
def mysql_session_factory():
    settings = Settings(_env_file=None, database_url=_disposable_url())
    engine = create_engine_for_settings(settings)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT @@transaction_isolation")) == (
            "READ-COMMITTED"
        )
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    try:
        yield factory
    finally:
        models.Base.metadata.drop_all(engine)
        engine.dispose()


def _seed(factory) -> tuple[models.KnowledgeDocument, bytes]:
    source = b"# MySQL source\nExact content"
    with session_scope(factory) as session:
        collection = models.Resource(
            user_id=7,
            resource_type="knowledge_collection",
            name="mysql-worker",
            config_json={},
        )
        session.add(collection)
        session.flush()
        document = models.KnowledgeDocument(
            id="mysql-document",
            user_id=7,
            collection_id=collection.id,
            name="source.md",
            source_object_key=(
                f"users/7/knowledge/{collection.id}/mysql-document/source"
            ),
            mime_type="text/markdown",
            size_bytes=len(source),
            content_hash=hashlib.sha256(source).hexdigest(),
            status="queued",
            index_generation=1,
            is_active=True,
        )
        session.add(document)
        session.flush()
        return document, source


def test_two_mysql_claimers_only_claim_one_generation(
    mysql_session_factory,
) -> None:
    document, _source = _seed(mysql_session_factory)
    claimed = Event()
    skipped = Event()
    release = Event()

    def attempt() -> str:
        with session_scope(mysql_session_factory) as session:
            item = KnowledgeDocumentRepository().claim_next(
                session,
                stale_before=models._now(),
            )
            if item is None:
                skipped.set()
                release.set()
                return "skipped"
            claimed.set()
            assert skipped.wait(timeout=30)
            assert release.wait(timeout=30)
            return item.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(attempt)
        second = pool.submit(attempt)
        assert claimed.wait(timeout=30)
        results = [first.result(timeout=60), second.result(timeout=60)]

    assert set(results) == {"skipped", document.id}
    with mysql_session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "processing"


def test_mysql_claim_identity_can_publish_and_rejects_late_claim(
    mysql_session_factory,
) -> None:
    document, _source = _seed(mysql_session_factory)
    repository = KnowledgeDocumentRepository()
    with session_scope(mysql_session_factory) as session:
        claim = repository.claim_next(
            session,
            stale_before=models._now(),
        )
    assert claim is not None
    with session_scope(mysql_session_factory) as session:
        assert repository.complete_generation(
            session,
            document_id=document.id,
            generation=1,
            processing_attempt_id=claim.processing_attempt_id,
            parsed_object_key="users/7/knowledge/parsed/1",
            retriever_type=claim.retriever_type,
        ) is True
    with session_scope(mysql_session_factory) as session:
        assert repository.complete_generation(
            session,
            document_id=document.id,
            generation=1,
            processing_attempt_id=claim.processing_attempt_id,
            parsed_object_key="users/7/knowledge/parsed/late",
            retriever_type=claim.retriever_type,
        ) is False


def test_worker_publishes_against_real_mysql_state_machine(
    mysql_session_factory,
) -> None:
    document, source = _seed(mysql_session_factory)
    store = FakeObjectStore()
    store.put(document.source_object_key, source, if_none_match=True)
    vectors = FakeKnowledgeVectorStore()
    worker = KnowledgeIndexWorker(
        session_factory=mysql_session_factory,
        repository=KnowledgeDocumentRepository(),
        object_store=store,
        extraction=ExtractionService(),
        retriever_factory=vectors,
        coordinator=DocumentOperationCoordinator(),
        clock=models._now,
        processing_timeout=timedelta(minutes=5),
        poll_interval=0.01,
    )

    assert worker.run_once() is True

    with mysql_session_factory() as session:
        current = session.get(models.KnowledgeDocument, document.id)
        assert current is not None
        assert current.status == "ready"
        assert current.parsed_object_key is not None
    assert vectors.has_generation(document.id, 1)
