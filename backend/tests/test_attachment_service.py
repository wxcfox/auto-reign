from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import hashlib
from uuid import UUID

from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.session import make_session_factory, session_scope
from app.repositories.attachment_repository import AttachmentRepository
from app.schemas.attachments import AttachmentResponse
from app.services.attachment_service import (
    AttachmentService,
    AttachmentServiceError,
)
from app.services.extraction_service import ExtractionError
from app.storage.object_store import (
    ObjectConflict,
    ObjectNotFound,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)
from tests.fake_object_store import FakeObjectStore


@dataclass(frozen=True)
class UserRef:
    id: int


@pytest.fixture
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'attachments.db'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    models.Base.metadata.create_all(engine)
    factory = make_session_factory(engine)
    yield factory
    engine.dispose()


def _create_user(factory: sessionmaker[Session], username: str) -> UserRef:
    with session_scope(factory) as session:
        row = models.User(username=username, password_hash="hash")
        session.add(row)
        session.flush()
        return UserRef(id=row.id)


@pytest.fixture
def user(session_factory: sessionmaker[Session]) -> UserRef:
    return _create_user(session_factory, "alice")


@pytest.fixture
def other_user(session_factory: sessionmaker[Session]) -> UserRef:
    return _create_user(session_factory, "bob")


def _create_attachment(
    factory: sessionmaker[Session],
    *,
    user_id: int,
    attachment_id: str = "draft-1",
    message_id: str | None = None,
) -> models.Attachment:
    content = b"source text"
    parsed = b"source text"
    with session_scope(factory) as session:
        row = models.Attachment(
            id=attachment_id,
            user_id=user_id,
            message_id=message_id,
            original_filename="notes.txt",
            object_key=f"users/{user_id}/attachments/{attachment_id}/notes.txt",
            parsed_object_key=f"users/{user_id}/attachments/{attachment_id}/parsed.txt",
            mime_type="text/plain",
            size_bytes=len(content),
            content_hash=hashlib.sha256(content).hexdigest(),
            parsed_size_bytes=len(parsed),
            parsed_content_hash=hashlib.sha256(parsed).hexdigest(),
        )
        session.add(row)
        session.flush()
        return row


@pytest.fixture
def draft_attachment(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> models.Attachment:
    return _create_attachment(session_factory, user_id=user.id)


@pytest.fixture
def bound_attachment(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> models.Attachment:
    with session_scope(session_factory) as session:
        agent = models.Resource(
            user_id=0,
            resource_type="agent",
            name="global-agent",
            config_json={},
        )
        session.add(agent)
        session.flush()
        conversation = models.Conversation(user_id=user.id, agent_id=agent.id)
        session.add(conversation)
        session.flush()
        message = models.Message(
            user_id=user.id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            status="completed",
            content="message",
        )
        session.add(message)
        session.flush()
        message_id = message.id
    return _create_attachment(
        session_factory,
        user_id=user.id,
        attachment_id="bound-1",
        message_id=message_id,
    )


@pytest.fixture
def populated_store(draft_attachment: models.Attachment) -> FakeObjectStore:
    store = FakeObjectStore()
    store.put(draft_attachment.object_key, b"source text")
    assert draft_attachment.parsed_object_key is not None
    store.put(draft_attachment.parsed_object_key, b"source text")
    return store


def test_upload_commits_before_returning_immutable_dto(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> None:
    store = FakeObjectStore()

    result = AttachmentService(
        store=store,
        session_factory=session_factory,
    ).create_draft_committed(
        user_id=user.id,
        filename="notes.txt",
        media_type="text/plain",
        content=b"source text",
    )

    assert result.object_key == f"users/{user.id}/attachments/{result.id}/notes.txt"
    assert store.get(result.object_key).data == b"source text"
    assert result.parsed_object_key is not None
    assert store.get(result.parsed_object_key).data == b"source text"
    assert result.message_id is None
    public_result = AttachmentResponse.model_validate(result).model_dump()
    assert "object_key" not in public_result
    assert "parsed_object_key" not in public_result
    with session_scope(session_factory) as independent:
        persisted = independent.scalar(
            select(models.Attachment).where(models.Attachment.id == result.id)
        )
        assert persisted is not None
        assert persisted.parsed_size_bytes == len(b"source text")
        assert persisted.parsed_content_hash == hashlib.sha256(b"source text").hexdigest()
    with pytest.raises(ValidationError):
        result.filename = "changed.txt"


def test_image_upload_has_no_parsed_object_or_integrity_fields(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> None:
    store = FakeObjectStore()

    result = AttachmentService(store=store, session_factory=session_factory).create_draft_committed(
        user_id=user.id,
        filename="diagram.png",
        media_type="image/png",
        content=b"image",
    )

    assert result.parsed_object_key is None
    assert store.keys() == [result.object_key]
    with session_scope(session_factory) as session:
        persisted = session.get(models.Attachment, result.id)
        assert persisted is not None
        assert persisted.parsed_size_bytes is None
        assert persisted.parsed_content_hash is None


def test_upload_parse_failure_or_size_failure_creates_nothing(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> None:
    store = FakeObjectStore()
    service = AttachmentService(store=store, session_factory=session_factory, max_bytes=3)

    with pytest.raises(ExtractionError, match="size") as too_large:
        service.create_draft_committed(
            user_id=user.id,
            filename="a.txt",
            media_type="text/plain",
            content=b"1234",
        )
    assert too_large.value.code == "extraction_too_large"
    with pytest.raises(ExtractionError) as unsupported:
        service.create_draft_committed(
            user_id=user.id,
            filename="a.zip",
            media_type="application/zip",
            content=b"zip",
        )
    assert unsupported.value.code == "extraction_unsupported"
    assert store.keys() == []


def test_upload_compensates_when_real_session_commit_fails(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> None:
    store = FakeObjectStore()
    service = AttachmentService(store=store, session_factory=session_factory)

    def fail_commit(_session) -> None:
        raise SQLAlchemyError("database unavailable")

    event.listen(session_factory.class_, "before_commit", fail_commit, once=True)
    with pytest.raises(SQLAlchemyError, match="database unavailable"):
        service.create_draft_committed(
            user_id=user.id,
            filename="notes.txt",
            media_type="text/plain",
            content=b"source text",
        )

    assert store.keys() == []
    with session_scope(session_factory) as independent:
        assert independent.scalar(select(models.Attachment)) is None


@pytest.mark.parametrize("failed_put", [1, 2])
def test_upload_compensates_an_uncertain_put_result(
    session_factory: sessionmaker[Session],
    user: UserRef,
    failed_put: int,
) -> None:
    store = FakeObjectStore(put_then_raise_on_call=failed_put)
    service = AttachmentService(store=store, session_factory=session_factory)

    with pytest.raises(ObjectStoreUnavailable, match="uncertain put"):
        service.create_draft_committed(
            user_id=user.id,
            filename="notes.txt",
            media_type="text/plain",
            content=b"source text",
        )

    assert store.keys() == []


@pytest.mark.parametrize("conflicting_object", ["source", "parsed"])
def test_conditional_put_conflict_never_deletes_the_preexisting_object(
    session_factory: sessionmaker[Session],
    user: UserRef,
    monkeypatch,
    conflicting_object: str,
) -> None:
    attachment_id = "00000000-0000-0000-0000-000000000123"
    monkeypatch.setattr(
        "app.services.attachment_service.uuid4",
        lambda: UUID(attachment_id),
    )
    prefix = f"users/{user.id}/attachments/{attachment_id}"
    source_key = f"{prefix}/notes.txt"
    parsed_key = f"{prefix}/parsed.txt"
    conflicting_key = source_key if conflicting_object == "source" else parsed_key
    store = FakeObjectStore()
    store.put(conflicting_key, b"preexisting")

    with pytest.raises(ObjectConflict):
        AttachmentService(
            store=store,
            session_factory=session_factory,
        ).create_draft_committed(
            user_id=user.id,
            filename="notes.txt",
            media_type="text/plain",
            content=b"source text",
        )

    assert store.get(conflicting_key).data == b"preexisting"
    assert store.keys() == [conflicting_key]
    assert conflicting_key not in store.delete_calls


def test_compensation_failure_does_not_mask_or_log_database_error_details(
    session_factory: sessionmaker[Session],
    user: UserRef,
    caplog,
) -> None:
    store = FakeObjectStore(delete_error=ObjectStoreUnavailable("cleanup secret"))
    service = AttachmentService(store=store, session_factory=session_factory)

    def fail_commit(_session) -> None:
        raise SQLAlchemyError("original commit error")

    event.listen(session_factory.class_, "before_commit", fail_commit, once=True)
    with pytest.raises(SQLAlchemyError, match="original commit error"):
        service.create_draft_committed(
            user_id=user.id,
            filename="notes.txt",
            media_type="text/plain",
            content=b"source text",
        )

    assert len(caplog.records) == 2
    for record in caplog.records:
        assert record.getMessage() == "attachment_compensation_failed"
        assert record.attachment_id
        assert record.exception_type == "ObjectStoreUnavailable"
        assert record.error_code == "attachment_compensation_failed"
        assert not record.exc_info
        assert "cleanup secret" not in record.getMessage()
        assert "users/" not in record.getMessage()


def test_delete_object_failure_rolls_back_and_preserves_draft(
    session_factory: sessionmaker[Session],
    user: UserRef,
    draft_attachment: models.Attachment,
) -> None:
    store = FakeObjectStore(delete_error=ObjectStoreUnavailable("offline"))
    service = AttachmentService(store=store, session_factory=session_factory)

    with pytest.raises(ObjectStoreUnavailable):
        service.delete_draft(user_id=user.id, attachment_id=draft_attachment.id)

    with session_scope(session_factory) as independent:
        assert independent.get(models.Attachment, draft_attachment.id) is not None


def test_delete_commit_failure_preserves_row_and_retry_converges(
    session_factory: sessionmaker[Session],
    user: UserRef,
    draft_attachment: models.Attachment,
    populated_store: FakeObjectStore,
) -> None:
    service = AttachmentService(store=populated_store, session_factory=session_factory)

    def fail_commit(_session) -> None:
        raise SQLAlchemyError("delete commit failed")

    event.listen(session_factory.class_, "before_commit", fail_commit, once=True)
    with pytest.raises(SQLAlchemyError, match="delete commit failed"):
        service.delete_draft(user_id=user.id, attachment_id=draft_attachment.id)
    with session_scope(session_factory) as independent:
        assert independent.get(models.Attachment, draft_attachment.id) is not None

    assert populated_store.keys() == []
    service.delete_draft(user_id=user.id, attachment_id=draft_attachment.id)
    with session_scope(session_factory) as independent:
        assert independent.get(models.Attachment, draft_attachment.id) is None


def test_delete_rejects_bound_missing_and_cross_user_attachments_without_object_calls(
    session_factory: sessionmaker[Session],
    user: UserRef,
    other_user: UserRef,
    draft_attachment: models.Attachment,
    bound_attachment: models.Attachment,
) -> None:
    store = FakeObjectStore()
    service = AttachmentService(store=store, session_factory=session_factory)

    for owner_id, attachment_id in (
        (user.id, bound_attachment.id),
        (user.id, "missing"),
        (other_user.id, draft_attachment.id),
    ):
        with pytest.raises(AttachmentServiceError) as captured:
            service.delete_draft(user_id=owner_id, attachment_id=attachment_id)
        assert captured.value.code == "attachment_not_ready"

    assert store.delete_calls == []


def test_list_drafts_is_committed_immutable_ordered_and_user_scoped(
    session_factory: sessionmaker[Session],
    user: UserRef,
    other_user: UserRef,
    bound_attachment: models.Attachment,
) -> None:
    first = _create_attachment(
        session_factory,
        user_id=user.id,
        attachment_id="draft-a",
    )
    second = _create_attachment(
        session_factory,
        user_id=user.id,
        attachment_id="draft-b",
    )
    _create_attachment(
        session_factory,
        user_id=other_user.id,
        attachment_id="other-draft",
    )
    service = AttachmentService(store=FakeObjectStore(), session_factory=session_factory)

    drafts = service.list_drafts(user_id=user.id)

    assert [draft.id for draft in drafts] == [first.id, second.id]
    assert bound_attachment.id not in {draft.id for draft in drafts}
    assert all(draft.message_id is None for draft in drafts)
    with pytest.raises(ValidationError):
        drafts[0].filename = "changed"


def test_read_original_returns_verified_content_outside_the_session(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> None:
    store = FakeObjectStore()
    service = AttachmentService(store=store, session_factory=session_factory)
    draft = service.create_draft_committed(
        user_id=user.id,
        filename="notes.txt",
        media_type="text/plain",
        content=b"source text",
    )

    result = service.read_original(user_id=user.id, attachment_id=draft.id)

    assert result.filename == "notes.txt"
    assert result.mime_type == "text/plain"
    assert result.content == b"source text"
    assert result.message_id is None


@pytest.mark.parametrize(
    ("get_error", "code"),
    [
        (ObjectNotFound("missing"), "attachment_unavailable"),
        (ObjectStoreUnavailable("offline"), "attachment_unavailable"),
        (ObjectTooLarge("oversized"), "attachment_corrupt"),
    ],
)
def test_read_original_maps_store_errors_without_exposing_object_key(
    session_factory: sessionmaker[Session],
    user: UserRef,
    get_error: Exception,
    code: str,
) -> None:
    row = _create_attachment(session_factory, user_id=user.id)
    service = AttachmentService(
        store=FakeObjectStore(get_error=get_error),
        session_factory=session_factory,
    )

    with pytest.raises(AttachmentServiceError) as captured:
        service.read_original(user_id=user.id, attachment_id=row.id)

    assert captured.value.code == code
    assert row.object_key not in str(captured.value)


def test_read_original_rejects_cross_user_and_hash_or_size_mismatch(
    session_factory: sessionmaker[Session],
    user: UserRef,
    other_user: UserRef,
) -> None:
    store = FakeObjectStore()
    service = AttachmentService(store=store, session_factory=session_factory)
    draft = service.create_draft_committed(
        user_id=user.id,
        filename="notes.txt",
        media_type="text/plain",
        content=b"source text",
    )

    with pytest.raises(AttachmentServiceError) as hidden:
        service.read_original(user_id=other_user.id, attachment_id=draft.id)
    assert hidden.value.code == "attachment_not_found"

    store.replace(draft.object_key, b"tampered")
    with pytest.raises(AttachmentServiceError) as corrupt:
        service.read_original(user_id=user.id, attachment_id=draft.id)
    assert corrupt.value.code == "attachment_corrupt"


def test_object_key_sanitizes_filename_and_avoids_parsed_object_collision(
    session_factory: sessionmaker[Session],
    user: UserRef,
) -> None:
    store = FakeObjectStore()
    service = AttachmentService(store=store, session_factory=session_factory)

    draft = service.create_draft_committed(
        user_id=user.id,
        filename="parsed.txt",
        media_type="text/plain",
        content=b"text",
    )

    assert draft.object_key.endswith("/source-parsed.txt")
    assert draft.parsed_object_key is not None
    assert draft.object_key != draft.parsed_object_key
    assert store.keys() == sorted([draft.object_key, draft.parsed_object_key])


def test_repository_bind_and_message_lookup_are_user_scoped(
    session_factory: sessionmaker[Session],
    user: UserRef,
    other_user: UserRef,
) -> None:
    repository = AttachmentRepository()
    first = _create_attachment(session_factory, user_id=user.id, attachment_id="first")
    other = _create_attachment(session_factory, user_id=other_user.id, attachment_id="other")
    with session_scope(session_factory) as session:
        agent = models.Resource(
            user_id=0,
            resource_type="agent",
            name="repository-agent",
            config_json={},
        )
        session.add(agent)
        session.flush()
        conversation = models.Conversation(user_id=user.id, agent_id=agent.id)
        session.add(conversation)
        session.flush()
        message = models.Message(
            id="message-1",
            user_id=user.id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            status="completed",
            content="message",
        )
        session.add(message)

    with session_scope(session_factory) as session:
        drafts = repository.lock_drafts(
            session,
            user_id=user.id,
            attachment_ids=[first.id, other.id],
        )
        assert [row.id for row in drafts] == [first.id]
        repository.bind_to_message(
            session,
            user_id=user.id,
            attachments=drafts,
            message_id="message-1",
        )

    with session_scope(session_factory) as session:
        assert [
            row.id
            for row in repository.list_for_messages(
                session,
                user_id=user.id,
                message_ids=["message-1"],
            )
        ] == [first.id]
        assert repository.list_for_messages(
            session,
            user_id=other_user.id,
            message_ids=["message-1"],
        ) == []
