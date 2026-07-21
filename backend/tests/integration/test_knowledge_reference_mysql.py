from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import os
from threading import Event, Lock
from uuid import uuid4

from fastapi import HTTPException
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from app.core.config import Settings
from app.db import models
from app.db.session import (
    create_engine_for_settings,
    make_session_factory,
    session_scope,
)
from app.schemas.agents import AgentConfig, AgentPutRequest, KnowledgeScope
from app.services.agent_service import AgentService
from app.services.knowledge_collection_service import KnowledgeCollectionService
from app.services.knowledge_document_service import (
    InactiveDocumentCleanup,
    KnowledgeCleanupError,
    KnowledgeDocumentService,
)
from app.services.upload_validation_service import ValidatedUpload
from tests.fake_object_store import FakeObjectStore
from tests.fakes import FakeKnowledgeVectorStore


@dataclass(frozen=True)
class _State:
    user_id: int
    collection_id: str
    agent_id: str
    agent_name: str
    document_id: str | None


class _UnsafeDisposableDatabaseError(ValueError):
    pass


def _normalized_host(host: str | None) -> str:
    normalized = (host or "").casefold().rstrip(".")
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return "loopback"
    return normalized


def _identity(url: URL) -> tuple[str, int, str | None]:
    return (
        _normalized_host(url.host),
        url.port or 3306,
        url.database.casefold() if url.database else None,
    )


def _disposable_mysql_url() -> URL:
    if os.environ.get("RUN_MYSQL_INTEGRATION") != "1":
        pytest.skip("requires RUN_MYSQL_INTEGRATION=1")
    explicit = os.environ.get("MYSQL_RESOURCE_RACE_DATABASE_URL")
    if not explicit:
        pytest.fail(
            "RUN_MYSQL_INTEGRATION=1 requires an explicit disposable "
            "MYSQL_RESOURCE_RACE_DATABASE_URL"
        )
    try:
        url = make_url(explicit)
    except ArgumentError as error:
        pytest.fail(
            "MYSQL_RESOURCE_RACE_DATABASE_URL is not a valid database URL"
        )
        raise AssertionError from error
    if not url.drivername.startswith("mysql") or not url.database:
        pytest.fail(
            "MYSQL_RESOURCE_RACE_DATABASE_URL must name a disposable MySQL database"
        )
    if url.database.casefold() in {
        "information_schema",
        "mysql",
        "performance_schema",
        "sys",
    }:
        pytest.fail("MYSQL_RESOURCE_RACE_DATABASE_URL must not name a system database")
    try:
        default_url = make_url(Settings(_env_file=None).database_url)
    except ArgumentError as error:
        raise _UnsafeDisposableDatabaseError(
            "cannot prove the race database differs from DATABASE_URL"
        ) from error
    if _identity(url) == _identity(default_url) or (
        default_url.database
        and url.database.casefold() == default_url.database.casefold()
    ):
        raise _UnsafeDisposableDatabaseError(
            "the disposable race database must differ from DATABASE_URL"
        )
    return url


def test_integration_flag_requires_explicit_disposable_url(monkeypatch) -> None:
    monkeypatch.setenv("RUN_MYSQL_INTEGRATION", "1")
    monkeypatch.delenv("MYSQL_RESOURCE_RACE_DATABASE_URL", raising=False)

    with pytest.raises(pytest.fail.Exception, match="explicit disposable"):
        _disposable_mysql_url()


@pytest.fixture
def mysql_session_factory():
    try:
        url = _disposable_mysql_url()
    except _UnsafeDisposableDatabaseError as error:
        pytest.fail(str(error))
    settings = Settings(
        _env_file=None,
        database_url=url.render_as_string(hide_password=False),
    )
    engine = create_engine_for_settings(settings)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT @@transaction_isolation")) == (
            "READ-COMMITTED"
        )
        assert connection.scalar(text("SELECT DATABASE()")) == url.database
    models.Base.metadata.drop_all(engine)
    models.Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    try:
        yield factory, settings
    finally:
        models.Base.metadata.drop_all(engine)
        engine.dispose()


def _setup(factory, *, with_document: bool) -> _State:
    suffix = uuid4().hex[:8]
    with factory() as session:
        user = models.User(
            username=f"knowledge-race-{suffix}",
            password_hash="not-used",
            display_name="Race User",
            role="user",
            is_active=True,
            token_version=1,
            settings_json={},
        )
        session.add(user)
        session.flush()
        collection = models.Resource(
            user_id=user.id,
            resource_type="knowledge_collection",
            name=f"collection-{suffix}",
            config_json={},
        )
        agent = models.Resource(
            user_id=user.id,
            resource_type="agent",
            name=f"agent-{suffix}",
            config_json=AgentConfig(system_prompt="Race.").model_dump(mode="json"),
        )
        session.add_all([collection, agent])
        session.flush()
        document_id: str | None = None
        if with_document:
            source = b"source"
            document = models.KnowledgeDocument(
                user_id=user.id,
                collection_id=collection.id,
                name="source.txt",
                source_object_key=(
                    f"users/{user.id}/knowledge/{collection.id}/fixture/source"
                ),
                parsed_object_key=None,
                mime_type="text/plain",
                size_bytes=len(source),
                content_hash=hashlib.sha256(source).hexdigest(),
                status="ready",
                index_generation=1,
                is_active=True,
            )
            session.add(document)
            session.flush()
            document_id = document.id
        session.commit()
        return _State(
            user_id=user.id,
            collection_id=collection.id,
            agent_id=agent.id,
            agent_name=agent.name,
            document_id=document_id,
        )


def _agent_payload(state: _State, *, exact: bool) -> AgentPutRequest:
    assert state.document_id is not None or not exact
    return AgentPutRequest(
        name=state.agent_name,
        config=AgentConfig(
            system_prompt="Race.",
            knowledge_scopes=[
                KnowledgeScope(
                    collection_id=state.collection_id,
                    document_ids=[state.document_id] if exact else None,
                )
            ],
        ),
        is_active=True,
    )


def _error_code(error: HTTPException) -> str:
    detail = error.detail
    assert isinstance(detail, dict)
    code = detail.get("code")
    assert isinstance(code, str)
    return code


def _bind_first_then_manage(
    factory,
    settings: Settings,
    state: _State,
    *,
    exact: bool,
) -> tuple[str, str]:
    auth_read = Event()
    bind_committed = Event()

    def manager() -> str:
        with factory() as session:
            actor = session.scalar(
                select(models.User).where(models.User.id == state.user_id)
            )
            assert actor is not None
            auth_read.set()
            assert bind_committed.wait(timeout=30)
            try:
                if exact:
                    assert state.document_id is not None
                    KnowledgeDocumentService(FakeObjectStore()).isolate_for_delete(
                        session,
                        actor=actor,
                        document_id=state.document_id,
                        cleanup_attempt_id="bind-first-cleanup",
                    )
                else:
                    KnowledgeCollectionService().delete_resource(
                        session,
                        actor=actor,
                        resource_id=state.collection_id,
                    )
                session.commit()
                return "deleted"
            except HTTPException as error:
                session.rollback()
                return _error_code(error)

    def binder() -> str:
        assert auth_read.wait(timeout=30)
        with factory() as session:
            actor = session.get(models.User, state.user_id)
            assert actor is not None
            AgentService(settings=settings).put_agent(
                session,
                actor=actor,
                agent_id=state.agent_id,
                payload=_agent_payload(state, exact=exact),
            )
            session.commit()
        bind_committed.set()
        return "bound"

    with ThreadPoolExecutor(max_workers=2) as pool:
        manager_future = pool.submit(manager)
        binder_future = pool.submit(binder)
        return binder_future.result(timeout=60), manager_future.result(timeout=60)


def _manage_first_then_bind(
    factory,
    settings: Settings,
    state: _State,
    *,
    exact: bool,
) -> tuple[str, str]:
    management_locked = Event()
    bind_reached_collection_lock = Event()
    binder_finished = Event()

    def manager() -> str:
        with factory() as session:
            actor = session.scalar(
                select(models.User).where(models.User.id == state.user_id)
            )
            assert actor is not None
            if exact:
                assert state.document_id is not None
                KnowledgeDocumentService(FakeObjectStore()).isolate_for_delete(
                    session,
                    actor=actor,
                    document_id=state.document_id,
                    cleanup_attempt_id="manage-first-cleanup",
                )
            else:
                KnowledgeCollectionService().delete_resource(
                    session,
                    actor=actor,
                    resource_id=state.collection_id,
                )
            management_locked.set()
            assert bind_reached_collection_lock.wait(timeout=30)
            assert not binder_finished.is_set()
            session.commit()
        return "deleted"

    def binder() -> str:
        try:
            assert management_locked.wait(timeout=30)
            with factory() as session:

                @event.listens_for(session, "do_orm_execute")
                def observe_collection_lock(orm_execute_state) -> None:
                    statement = orm_execute_state.statement
                    if (
                        orm_execute_state.is_select
                        and statement._for_update_arg is not None
                        and "resources.id IN" in str(statement)
                    ):
                        bind_reached_collection_lock.set()

                actor = session.get(models.User, state.user_id)
                assert actor is not None
                try:
                    AgentService(settings=settings).put_agent(
                        session,
                        actor=actor,
                        agent_id=state.agent_id,
                        payload=_agent_payload(state, exact=exact),
                    )
                    session.commit()
                    return "bound"
                except HTTPException as error:
                    session.rollback()
                    return _error_code(error)
        finally:
            bind_reached_collection_lock.set()
            binder_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        manager_future = pool.submit(manager)
        binder_future = pool.submit(binder)
        return manager_future.result(timeout=60), binder_future.result(timeout=60)


@pytest.mark.parametrize("ordering", ["bind_first", "delete_first"])
def test_delete_serializes_with_new_exact_agent_binding(
    mysql_session_factory,
    ordering: str,
) -> None:
    factory, settings = mysql_session_factory
    state = _setup(factory, with_document=True)
    if ordering == "bind_first":
        result = _bind_first_then_manage(
            factory,
            settings,
            state,
            exact=True,
        )
        assert result == ("bound", "resource_in_use")
    else:
        result = _manage_first_then_bind(
            factory,
            settings,
            state,
            exact=True,
        )
        assert result == ("deleted", "resource_reference_invalid")


@pytest.mark.parametrize("ordering", ["bind_first", "delete_first"])
def test_collection_delete_serializes_with_new_agent_binding(
    mysql_session_factory,
    ordering: str,
) -> None:
    factory, settings = mysql_session_factory
    state = _setup(factory, with_document=False)
    if ordering == "bind_first":
        result = _bind_first_then_manage(
            factory,
            settings,
            state,
            exact=False,
        )
        assert result == ("bound", "resource_in_use")
    else:
        result = _manage_first_then_bind(
            factory,
            settings,
            state,
            exact=False,
        )
        assert result == ("deleted", "resource_reference_invalid")


class _PauseAfterPutStore(FakeObjectStore):
    def __init__(self, put_finished: Event, release_put: Event) -> None:
        super().__init__()
        self._put_finished = put_finished
        self._release_put = release_put

    def put(self, key, data, if_none_match=False, expected_etag=None):
        metadata = super().put(
            key,
            data,
            if_none_match=if_none_match,
            expected_etag=expected_etag,
        )
        self._put_finished.set()
        assert self._release_put.wait(timeout=30)
        return metadata


def _validated_upload() -> ValidatedUpload:
    content = b"source"
    return ValidatedUpload(
        filename="source.txt",
        mime_type="text/plain",
        content=content,
        size_bytes=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )


def _upload_delete_first(factory, state: _State) -> tuple[str, str, FakeObjectStore]:
    put_finished = Event()
    release_put = Event()
    store = _PauseAfterPutStore(put_finished, release_put)

    def uploader() -> str:
        try:
            KnowledgeDocumentService(store).upload_committed(
                factory,
                actor_id=state.user_id,
                collection_id=state.collection_id,
                upload=_validated_upload(),
            )
            return "document_committed"
        except HTTPException as error:
            return _error_code(error)

    def deleter() -> str:
        assert put_finished.wait(timeout=30)
        with factory() as session:
            actor = session.scalar(
                select(models.User).where(models.User.id == state.user_id)
            )
            assert actor is not None
            KnowledgeCollectionService().delete_resource(
                session,
                actor=actor,
                resource_id=state.collection_id,
            )
            session.commit()
        release_put.set()
        return "deleted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        upload_future = pool.submit(uploader)
        delete_future = pool.submit(deleter)
        return (
            upload_future.result(timeout=60),
            delete_future.result(timeout=60),
            store,
        )


def _upload_first(factory, state: _State) -> tuple[str, str, FakeObjectStore]:
    write_flushed = Event()
    delete_reached_lock = Event()
    delete_finished = Event()
    factory_lock = Lock()
    factory_calls = 0
    store = FakeObjectStore()

    def pausing_factory():
        nonlocal factory_calls
        with factory_lock:
            factory_calls += 1
            call_number = factory_calls
        session = factory()
        if call_number == 2:

            @event.listens_for(session, "after_flush")
            def pause_after_document_flush(_session, _context) -> None:
                if write_flushed.is_set():
                    return
                write_flushed.set()
                assert delete_reached_lock.wait(timeout=30)
                assert not delete_finished.is_set()

        return session

    def uploader() -> str:
        KnowledgeDocumentService(store).upload_committed(
            pausing_factory,
            actor_id=state.user_id,
            collection_id=state.collection_id,
            upload=_validated_upload(),
        )
        return "document_committed"

    def deleter() -> str:
        try:
            assert write_flushed.wait(timeout=30)
            with factory() as session:
                actor = session.scalar(
                    select(models.User).where(models.User.id == state.user_id)
                )
                assert actor is not None

                @event.listens_for(session, "do_orm_execute")
                def observe_collection_lock(orm_execute_state) -> None:
                    statement = orm_execute_state.statement
                    if (
                        orm_execute_state.is_select
                        and statement._for_update_arg is not None
                        and "resources" in str(statement)
                    ):
                        delete_reached_lock.set()

                try:
                    KnowledgeCollectionService().delete_resource(
                        session,
                        actor=actor,
                        resource_id=state.collection_id,
                    )
                    session.commit()
                    return "deleted"
                except HTTPException as error:
                    session.rollback()
                    return _error_code(error)
        finally:
            delete_reached_lock.set()
            delete_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        upload_future = pool.submit(uploader)
        delete_future = pool.submit(deleter)
        return (
            upload_future.result(timeout=60),
            delete_future.result(timeout=60),
            store,
        )


@pytest.mark.parametrize("ordering", ["upload_first", "delete_first"])
def test_upload_serializes_with_collection_delete(
    mysql_session_factory,
    ordering: str,
) -> None:
    factory, _settings = mysql_session_factory
    state = _setup(factory, with_document=False)
    if ordering == "upload_first":
        upload_result, delete_result, store = _upload_first(factory, state)
        assert (upload_result, delete_result) == (
            "document_committed",
            "resource_in_use",
        )
        assert len(store.keys()) == 1
    else:
        upload_result, delete_result, store = _upload_delete_first(factory, state)
        assert (upload_result, delete_result) == (
            "knowledge_collection_not_found",
            "deleted",
        )
        assert store.keys() == []
        assert len(store.delete_calls) == 1


def test_document_cleanup_isolation_blocks_collection_delete(
    mysql_session_factory,
) -> None:
    factory, _settings = mysql_session_factory
    state = _setup(factory, with_document=True)
    assert state.document_id is not None
    isolated = Event()
    delete_reached_lock = Event()
    delete_finished = Event()

    def isolator() -> str:
        with factory() as session:
            actor = session.scalar(
                select(models.User).where(models.User.id == state.user_id)
            )
            assert actor is not None
            KnowledgeDocumentService(FakeObjectStore()).isolate_for_delete(
                session,
                actor=actor,
                document_id=state.document_id,
                cleanup_attempt_id="isolation-cleanup",
            )
            isolated.set()
            assert delete_reached_lock.wait(timeout=30)
            assert not delete_finished.is_set()
            session.commit()
        return "isolated"

    def deleter() -> str:
        try:
            assert isolated.wait(timeout=30)
            with factory() as session:
                actor = session.scalar(
                    select(models.User).where(models.User.id == state.user_id)
                )
                assert actor is not None

                @event.listens_for(session, "do_orm_execute")
                def observe_collection_lock(orm_execute_state) -> None:
                    statement = orm_execute_state.statement
                    if (
                        orm_execute_state.is_select
                        and statement._for_update_arg is not None
                        and "resources" in str(statement)
                    ):
                        delete_reached_lock.set()

                try:
                    KnowledgeCollectionService().delete_resource(
                        session,
                        actor=actor,
                        resource_id=state.collection_id,
                    )
                    session.commit()
                    return "deleted"
                except HTTPException as error:
                    session.rollback()
                    return _error_code(error)
        finally:
            delete_reached_lock.set()
            delete_finished.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        isolate_future = pool.submit(isolator)
        delete_future = pool.submit(deleter)
        assert isolate_future.result(timeout=60) == "isolated"
        assert delete_future.result(timeout=60) == "resource_in_use"


def _collection_delete_result(factory, state: _State) -> str:
    with factory() as session:
        actor = session.get(models.User, state.user_id)
        assert actor is not None
        try:
            KnowledgeCollectionService().delete_resource(
                session,
                actor=actor,
                resource_id=state.collection_id,
            )
            session.commit()
            return "deleted"
        except HTTPException as error:
            session.rollback()
            return _error_code(error)


def _cleanup_snapshot(factory, state: _State) -> InactiveDocumentCleanup:
    assert state.document_id is not None
    with factory() as session:
        document = session.get(models.KnowledgeDocument, state.document_id)
        assert document is not None
        return InactiveDocumentCleanup(
            id=document.id,
            user_id=document.user_id,
            collection_id=document.collection_id,
            retriever_type=document.retriever_type,  # type: ignore[arg-type]
        )


def test_cleanup_success_commit_releases_collection_delete(
    mysql_session_factory,
) -> None:
    factory, _settings = mysql_session_factory
    state = _setup(factory, with_document=True)
    assert state.document_id is not None
    store = FakeObjectStore()
    vectors = FakeKnowledgeVectorStore()
    service = KnowledgeDocumentService(store, retriever_factory=vectors)
    isolated_commit = Event()
    allow_cleanup = Event()
    cleanup_attempt_id = "cleanup-success-attempt"

    def delete_document() -> str:
        with factory() as session:
            actor = session.get(models.User, state.user_id)
            assert actor is not None
            service.isolate_for_delete(
                session,
                actor=actor,
                document_id=state.document_id,
                cleanup_attempt_id=cleanup_attempt_id,
            )
            session.commit()
        isolated_commit.set()
        assert allow_cleanup.wait(timeout=30)
        service.cleanup_inactive(_cleanup_snapshot(factory, state))
        with session_scope(factory) as session:
            service.repository.clear_cleanup_error_if_inactive(
                session,
                document_id=state.document_id,
                cleanup_attempt_id=cleanup_attempt_id,
            )
        return "cleaned"

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(delete_document)
        assert isolated_commit.wait(timeout=30)
        assert _collection_delete_result(factory, state) == "resource_in_use"
        with factory() as session:
            pending = session.get(models.KnowledgeDocument, state.document_id)
            assert pending is not None
            assert pending.is_active is False
            assert pending.error_code == "knowledge_cleanup_pending"
        allow_cleanup.set()
        assert future.result(timeout=60) == "cleaned"

    assert _collection_delete_result(factory, state) == "deleted"


def test_cleanup_failure_blocks_collection_until_explicit_retry_succeeds(
    mysql_session_factory,
) -> None:
    factory, _settings = mysql_session_factory
    state = _setup(factory, with_document=True)
    assert state.document_id is not None
    store = FakeObjectStore()
    vectors = FakeKnowledgeVectorStore()
    vectors.fail("delete_document")
    service = KnowledgeDocumentService(store, retriever_factory=vectors)
    isolated_commit = Event()
    allow_cleanup = Event()
    first_cleanup_attempt_id = "cleanup-failure-attempt"

    def first_delete_attempt() -> str:
        with factory() as session:
            actor = session.get(models.User, state.user_id)
            assert actor is not None
            service.isolate_for_delete(
                session,
                actor=actor,
                document_id=state.document_id,
                cleanup_attempt_id=first_cleanup_attempt_id,
            )
            session.commit()
        isolated_commit.set()
        assert allow_cleanup.wait(timeout=30)
        try:
            service.cleanup_inactive(_cleanup_snapshot(factory, state))
        except KnowledgeCleanupError:
            with session_scope(factory) as session:
                service.repository.mark_cleanup_failed_if_inactive(
                    session,
                    document_id=state.document_id,
                    cleanup_attempt_id=first_cleanup_attempt_id,
                    message="Knowledge cleanup failed.",
                )
            return "cleanup_failed"
        raise AssertionError("cleanup failure was expected")

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_delete_attempt)
        assert isolated_commit.wait(timeout=30)
        assert _collection_delete_result(factory, state) == "resource_in_use"
        allow_cleanup.set()
        assert future.result(timeout=60) == "cleanup_failed"

    with factory() as session:
        failed = session.get(models.KnowledgeDocument, state.document_id)
        assert failed is not None
        assert failed.error_code == "knowledge_cleanup_failed"
    assert _collection_delete_result(factory, state) == "resource_in_use"

    vectors.recover()
    retry_cleanup_attempt_id = "cleanup-retry-attempt"
    with factory() as session:
        actor = session.get(models.User, state.user_id)
        assert actor is not None
        service.isolate_for_delete(
            session,
            actor=actor,
            document_id=state.document_id,
            cleanup_attempt_id=retry_cleanup_attempt_id,
        )
        session.commit()
    service.cleanup_inactive(_cleanup_snapshot(factory, state))
    with session_scope(factory) as session:
        service.repository.clear_cleanup_error_if_inactive(
            session,
            document_id=state.document_id,
            cleanup_attempt_id=retry_cleanup_attempt_id,
        )
    assert _collection_delete_result(factory, state) == "deleted"
