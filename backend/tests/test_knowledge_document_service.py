from __future__ import annotations

import hashlib
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.db import models
from app.schemas.agents import AgentConfig, KnowledgeScope
from app.services.knowledge_document_service import KnowledgeDocumentService
from app.services.upload_validation_service import ValidatedUpload
from app.storage import ObjectConflict, ObjectStoreUnavailable
from tests.fake_object_store import FakeObjectStore


def _upload(
    *,
    filename: str = "guide.md",
    mime_type: str = "text/markdown",
    content: bytes = b"# Guide\nOriginal text",
) -> ValidatedUpload:
    return ValidatedUpload(
        filename=filename,
        mime_type=mime_type,
        content=content,
        size_bytes=len(content),
        content_hash=hashlib.sha256(content).hexdigest(),
    )


def _actor(session_factory, *, username: str = "alice") -> models.User:
    with session_factory() as session:
        actor = session.scalar(
            select(models.User).where(models.User.username == username)
        )
        assert actor is not None
        return actor


def _collection(
    session_factory,
    *,
    owner_id: int,
    name: str = "资料库",
) -> models.Resource:
    with session_factory() as session:
        collection = models.Resource(
            user_id=owner_id,
            resource_type="knowledge_collection",
            name=name,
            config_json={},
        )
        session.add(collection)
        session.commit()
        return collection


def _document(
    session_factory,
    *,
    owner_id: int,
    collection_id: str,
    is_active: bool = True,
    status: str = "ready",
) -> models.KnowledgeDocument:
    content = b"source"
    with session_factory() as session:
        document = models.KnowledgeDocument(
            user_id=owner_id,
            collection_id=collection_id,
            name="source.txt",
            source_object_key="users/fixture/knowledge/source",
            parsed_object_key="users/fixture/knowledge/parsed/1",
            mime_type="text/plain",
            size_bytes=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            status=status,
            index_generation=1,
            is_active=is_active,
        )
        session.add(document)
        session.commit()
        return document


def test_upload_uses_collection_owner_and_queues_generation(
    client,
    ordinary_user_headers,
    session_factory,
    fake_object_store,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)

    document = KnowledgeDocumentService(fake_object_store).upload_committed(
        session_factory,
        actor_id=user.id,
        collection_id=collection.id,
        upload=_upload(),
    )

    assert document.user_id == user.id
    assert document.status == "queued"
    assert document.index_generation == 1
    assert document.source_object_key == (
        f"users/{user.id}/knowledge/{collection.id}/{document.id}/source"
    )
    assert fake_object_store.keys() == [document.source_object_key]
    with session_factory() as session:
        persisted = session.get(models.KnowledgeDocument, document.id)
        assert persisted is not None
        assert persisted.status == "queued"


def test_global_collection_document_is_owned_by_sentinel_zero(
    client,
    admin_headers,
    session_factory,
    fake_object_store,
) -> None:
    del admin_headers
    admin = _actor(session_factory, username="admin")
    collection = _collection(
        session_factory,
        owner_id=0,
        name="全局资料库",
    )

    document = KnowledgeDocumentService(fake_object_store).upload_committed(
        session_factory,
        actor_id=admin.id,
        collection_id=collection.id,
        upload=_upload(
            filename="policy.pdf",
            mime_type="application/pdf",
            content=b"%PDF-test",
        ),
    )

    assert document.user_id == 0
    assert document.source_object_key.startswith("users/0/knowledge/")


def test_delete_rejects_document_in_exact_agent_scope(
    client,
    ordinary_user_headers,
    session_factory,
    fake_object_store,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)
    document = _document(
        session_factory,
        owner_id=user.id,
        collection_id=collection.id,
    )
    with session_factory() as session:
        session.add(
            models.Resource(
                user_id=user.id,
                resource_type="agent",
                name="精确引用助手",
                config_json=AgentConfig(
                    system_prompt="Use sources.",
                    knowledge_scopes=[
                        KnowledgeScope(
                            collection_id=collection.id,
                            document_ids=[document.id],
                        )
                    ],
                ).model_dump(mode="json"),
            )
        )
        session.commit()

    with session_factory() as session:
        actor = session.get(models.User, user.id)
        assert actor is not None
        with pytest.raises(HTTPException) as error:
            KnowledgeDocumentService(fake_object_store).isolate_for_delete(
                session,
                actor=actor,
                document_id=document.id,
                cleanup_attempt_id="cleanup-attempt-1",
            )
        session.rollback()

    assert error.value.detail["code"] == "resource_in_use"


def test_whole_collection_scope_does_not_block_single_document_isolation(
    client,
    ordinary_user_headers,
    session_factory,
    fake_object_store,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)
    document = _document(
        session_factory,
        owner_id=user.id,
        collection_id=collection.id,
    )
    with session_factory() as session:
        session.add(
            models.Resource(
                user_id=user.id,
                resource_type="agent",
                name="整库助手",
                config_json=AgentConfig(
                    system_prompt="Use sources.",
                    knowledge_scopes=[
                        KnowledgeScope(collection_id=collection.id)
                    ],
                ).model_dump(mode="json"),
            )
        )
        session.commit()
    with session_factory() as session:
        actor = session.get(models.User, user.id)
        assert actor is not None
        isolated = KnowledgeDocumentService(
            fake_object_store
        ).isolate_for_delete(
            session,
            actor=actor,
            document_id=document.id,
            cleanup_attempt_id="cleanup-attempt-1",
        )
        session.commit()
        assert isolated.is_active is False
        assert isolated.error_code == "knowledge_cleanup_pending"


def test_upload_commit_failure_compensates_source_object(
    client,
    ordinary_user_headers,
    session_factory,
    fake_object_store,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)

    call_count = 0

    def failing_factory():
        nonlocal call_count
        call_count += 1
        session = session_factory()
        if call_count == 2:

            @event.listens_for(session, "before_commit")
            def fail_commit(_session) -> None:
                raise SQLAlchemyError("forced commit failure")

        return session

    with pytest.raises(SQLAlchemyError, match="forced commit failure"):
        KnowledgeDocumentService(fake_object_store).upload_committed(
            failing_factory,
            actor_id=user.id,
            collection_id=collection.id,
            upload=_upload(content=b"source"),
        )

    assert fake_object_store.keys() == []
    with session_factory() as session:
        assert list(session.scalars(select(models.KnowledgeDocument))) == []


def test_upload_compensates_when_put_result_is_uncertain(
    client,
    ordinary_user_headers,
    session_factory,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)
    store = FakeObjectStore(put_then_raise_on_call=1)

    with pytest.raises(ObjectStoreUnavailable, match="uncertain"):
        KnowledgeDocumentService(store).upload_committed(
            session_factory,
            actor_id=user.id,
            collection_id=collection.id,
            upload=_upload(content=b"source"),
        )

    assert store.keys() == []
    assert len(store.delete_calls) == 1


def test_upload_does_not_delete_on_if_none_match_conflict(
    client,
    ordinary_user_headers,
    session_factory,
    monkeypatch,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)
    fixed = UUID("00000000-0000-0000-0000-000000000001")
    monkeypatch.setattr(
        "app.services.knowledge_document_service.uuid4",
        lambda: fixed,
    )
    key = KnowledgeDocumentService.source_key(
        user.id,
        collection.id,
        str(fixed),
    )
    store = FakeObjectStore()
    store.put(key, b"preexisting", if_none_match=True)

    with pytest.raises(ObjectConflict):
        KnowledgeDocumentService(store).upload_committed(
            session_factory,
            actor_id=user.id,
            collection_id=collection.id,
            upload=_upload(content=b"source"),
        )

    assert store.keys() == [key]
    assert store.delete_calls == []


def test_reindex_rejects_inactive_document(
    client,
    ordinary_user_headers,
    session_factory,
    fake_object_store,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)
    document = _document(
        session_factory,
        owner_id=user.id,
        collection_id=collection.id,
        is_active=False,
    )
    with session_factory() as session:
        actor = session.get(models.User, user.id)
        assert actor is not None
        with pytest.raises(HTTPException) as error:
            KnowledgeDocumentService(fake_object_store).reindex(
                session,
                actor=actor,
                document_id=document.id,
            )

    assert error.value.detail["code"] == "knowledge_document_not_found"


def test_reindex_increments_generation_and_clears_published_parse(
    client,
    ordinary_user_headers,
    session_factory,
    fake_object_store,
) -> None:
    del ordinary_user_headers
    user = _actor(session_factory)
    collection = _collection(session_factory, owner_id=user.id)
    document = _document(
        session_factory,
        owner_id=user.id,
        collection_id=collection.id,
    )
    with session_factory() as session:
        actor = session.get(models.User, user.id)
        assert actor is not None
        updated = KnowledgeDocumentService(fake_object_store).reindex(
            session,
            actor=actor,
            document_id=document.id,
        )
        session.commit()
        assert updated.index_generation == 2
        assert updated.status == "queued"
        assert updated.parsed_object_key is None
        assert updated.indexed_at is None
