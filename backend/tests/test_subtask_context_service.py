from __future__ import annotations

import base64
from collections.abc import Iterator
from dataclasses import dataclass, fields
from unittest.mock import ANY, Mock

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.repositories.subtask_context_repository import (
    SubtaskContextRepository,
    SubtaskContextRepositoryError,
    SubtaskRuntimeContextProjection,
)
from app.services.extraction_service import ExtractedContent, ExtractionError
from app.services.subtask_context_service import (
    SubtaskContextService,
    SubtaskContextServiceError,
)


@dataclass(frozen=True)
class _UserRef:
    id: int


class _Extraction:
    def extract(self, *, filename: str, media_type: str, content: bytes) -> ExtractedContent:
        del filename
        if content == b"parse-fails":
            raise ExtractionError("private_parser_detail", "do not expose this message")
        if content == b"unexpected-fails":
            raise RuntimeError("private unexpected parser message")
        if media_type.startswith("image/"):
            return ExtractedContent(kind="image", mime_type=media_type, text=None)
        return ExtractedContent(kind="text", mime_type=media_type, text=content.decode())


@pytest.fixture
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(f"sqlite:///{tmp_path / 'subtask-contexts.db'}")
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def users(session_factory: sessionmaker[Session]) -> tuple[_UserRef, _UserRef]:
    with session_factory.begin() as session:
        rows = [
            models.User(
                username=name,
                password_hash="not-used",
                display_name=name.title(),
                role="user",
                is_active=True,
                token_version=1,
                settings_json={},
            )
            for name in ("alice", "bob")
        ]
        session.add_all(rows)
        session.flush()
        return _UserRef(rows[0].id), _UserRef(rows[1].id)


@pytest.fixture
def service(session_factory: sessionmaker[Session]) -> SubtaskContextService:
    return SubtaskContextService(
        session_factory=session_factory,
        extraction=_Extraction(),  # type: ignore[arg-type]
    )


def test_text_and_image_uploads_preserve_mysql_authoritative_content(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
    service: SubtaskContextService,
) -> None:
    text = service.create_attachment_draft(
        user_id=users[0].id,
        filename="notes.txt",
        media_type="text/plain",
        content=b"exact source",
    )
    image_bytes = b"\x89PNG\r\n\x1a\nimage"
    image = service.create_attachment_draft(
        user_id=users[0].id,
        filename="diagram.png",
        media_type="image/png",
        content=image_bytes,
    )

    assert text.status == "ready"
    assert text.text_length == len("exact source")
    assert image.status == "ready"
    assert image.text_length == 0
    with session_factory() as session:
        text_row = session.get(models.SubtaskContext, text.id)
        image_row = session.get(models.SubtaskContext, image.id)
        assert text_row is not None
        assert text_row.binary_data == b"exact source"
        assert text_row.image_base64 is None
        assert text_row.extracted_text == "exact source"
        assert image_row is not None
        assert image_row.binary_data == image_bytes
        assert image_row.image_base64 == base64.b64encode(image_bytes).decode("ascii")
        assert image_row.extracted_text is None


def test_parse_failure_retains_original_and_only_safe_code(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
    service: SubtaskContextService,
) -> None:
    failed = service.create_attachment_draft(
        user_id=users[0].id,
        filename="bad.txt",
        media_type="text/plain",
        content=b"parse-fails",
    )
    failed_image = service.create_attachment_draft(
        user_id=users[0].id,
        filename="bad.png",
        media_type="image/png",
        content=b"parse-fails",
    )

    assert failed.status == "failed"
    assert failed_image.status == "failed"
    with session_factory() as session:
        row = session.get(models.SubtaskContext, failed.id)
        image_row = session.get(models.SubtaskContext, failed_image.id)
        assert row is not None
        assert row.binary_data == b"parse-fails"
        assert row.error_message == "private_parser_detail"
        assert "do not expose" not in row.error_message
        assert image_row is not None
        assert image_row.binary_data == b"parse-fails"
        assert image_row.image_base64 == base64.b64encode(b"parse-fails").decode("ascii")
        assert image_row.error_message == "private_parser_detail"


def test_unexpected_parse_failure_is_safely_persisted_then_reraised(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
    service: SubtaskContextService,
) -> None:
    with pytest.raises(RuntimeError, match="private unexpected parser message"):
        service.create_attachment_draft(
            user_id=users[0].id,
            filename="unexpected.png",
            media_type="image/png",
            content=b"unexpected-fails",
        )

    with session_factory() as session:
        row = session.scalar(
            select(models.SubtaskContext).where(
                models.SubtaskContext.name == "unexpected.png"
            )
        )
        assert row is not None
        assert row.status == "failed"
        assert row.error_message == "extraction_failed"
        assert row.binary_data == b"unexpected-fails"
        assert row.image_base64 == base64.b64encode(b"unexpected-fails").decode("ascii")


def test_unexpected_parse_failure_preserves_original_when_mark_failed_breaks(
    users: tuple[_UserRef, _UserRef],
    service: SubtaskContextService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_to_mark(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private failure persistence message")

    warning = Mock()
    monkeypatch.setattr(service.repository, "mark_failed", fail_to_mark)
    monkeypatch.setattr(
        "app.services.subtask_context_service.logger.warning",
        warning,
    )
    with pytest.raises(RuntimeError, match="private unexpected parser message"):
        service.create_attachment_draft(
            user_id=users[0].id,
            filename="unexpected.png",
            media_type="image/png",
            content=b"unexpected-fails",
        )

    warning.assert_called_once_with(
        "subtask_context_failure_persistence_failed",
        extra={
            "context_id": ANY,
            "error_code": "context_failure_persistence_failed",
            "exception_type": "RuntimeError",
        },
        exc_info=False,
    )
    serialized = repr(warning.call_args)
    assert "private failure persistence message" not in serialized
    assert "private unexpected parser message" not in serialized


def test_ordered_drafts_owner_isolation_content_and_delete(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
    service: SubtaskContextService,
) -> None:
    first = service.create_attachment_draft(
        user_id=users[0].id,
        filename="one.txt",
        media_type="text/plain",
        content=b"one",
    )
    second = service.create_attachment_draft(
        user_id=users[0].id,
        filename="two.txt",
        media_type="text/plain",
        content=b"two",
    )
    other = service.create_attachment_draft(
        user_id=users[1].id,
        filename="other.txt",
        media_type="text/plain",
        content=b"secret",
    )

    assert [item.id for item in service.list_drafts(user_id=users[0].id)] == [
        first.id,
        second.id,
    ]
    assert service.get_content(user_id=users[0].id, context_id=first.id).content == b"one"
    with pytest.raises(SubtaskContextServiceError, match="context_not_found"):
        service.get_content(user_id=users[0].id, context_id=other.id)
    with pytest.raises(SubtaskContextServiceError, match="context_not_found"):
        service.delete_draft(user_id=users[0].id, context_id=other.id)
    with pytest.raises(SubtaskContextServiceError, match="context_not_found"):
        service.delete_draft(user_id=users[0].id, context_id=999_999)
    service.delete_draft(user_id=users[0].id, context_id=first.id)
    assert [item.id for item in service.list_drafts(user_id=users[0].id)] == [second.id]

    with session_factory.begin() as session:
        SubtaskContextRepository().bind_drafts(
            session,
            user_id=users[0].id,
            context_ids=[second.id],
            subtask_id=77,
        )
    with pytest.raises(SubtaskContextServiceError, match="context_not_ready"):
        service.delete_draft(user_id=users[0].id, context_id=second.id)


def test_binding_preserves_request_order_and_rejects_invalid_batches_atomically(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
) -> None:
    repository = SubtaskContextRepository()
    with session_factory() as session:
        assert repository.bind_drafts(
            session,
            user_id=users[0].id,
            context_ids=[],
            subtask_id=91,
        ) == []
    with session_factory.begin() as session:
        first = repository.create_draft(
            session,
            user_id=users[0].id,
            context_type="attachment",
            name="first",
            status="ready",
        )
        second = repository.create_draft(
            session,
            user_id=users[0].id,
            context_type="attachment",
            name="second",
            status="ready",
        )
        pending = repository.create_draft(
            session,
            user_id=users[0].id,
            context_type="attachment",
            name="pending",
            status="parsing",
        )
        foreign = repository.create_draft(
            session,
            user_id=users[1].id,
            context_type="attachment",
            name="foreign",
            status="ready",
        )

    with session_factory.begin() as session:
        bound = repository.bind_drafts(
            session,
            user_id=users[0].id,
            context_ids=[second.id, first.id],
            subtask_id=91,
        )
        assert [row.id for row in bound] == [second.id, first.id]

    invalid_batches = [
        [pending.id, 999_999],
        [pending.id, foreign.id],
        [pending.id, pending.id],
        [pending.id, first.id],
    ]
    for context_ids in invalid_batches:
        with session_factory() as session:
            with pytest.raises(SubtaskContextRepositoryError, match="context_not_ready"):
                repository.bind_drafts(
                    session,
                    user_id=users[0].id,
                    context_ids=context_ids,
                    subtask_id=92,
                )
            session.rollback()
        with session_factory() as session:
            persisted = session.get(models.SubtaskContext, pending.id)
            assert persisted is not None
            assert persisted.subtask_id == 0


def test_selected_documents_persists_only_ordered_ids_and_validates(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
    service: SubtaskContextService,
) -> None:
    selected = service.create_selected_documents_draft(
        user_id=users[0].id,
        knowledge_id=" knowledge-1 ",
        document_ids=[" document-2", "document-1 "],
    )
    assert selected.context_type == "selected_documents"
    assert selected.type_data == {
        "knowledge_id": "knowledge-1",
        "document_ids": ["document-2", "document-1"],
    }
    with session_factory() as session:
        row = session.get(models.SubtaskContext, selected.id)
        assert row is not None
        assert row.type_data == selected.type_data
        assert row.binary_data is None
        assert row.image_base64 is None
        assert row.extracted_text is None

    invalid = [
        ([], "knowledge-1"),
        (["same", "same"], "knowledge-1"),
        (["doc", " doc "], "knowledge-1"),
        ([""], "knowledge-1"),
    ]
    for document_ids, knowledge_id in invalid:
        with pytest.raises(SubtaskContextServiceError, match="context_invalid"):
            service.create_selected_documents_draft(
                user_id=users[0].id,
                knowledge_id=knowledge_id,
                document_ids=document_ids,
            )


def test_repository_list_for_subtasks_is_deterministic_and_owner_scoped(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
) -> None:
    repository = SubtaskContextRepository()
    with session_factory.begin() as session:
        owned = repository.create_draft(
            session,
            user_id=users[0].id,
            context_type="knowledge_base",
            name="owned",
            status="ready",
        )
        foreign = repository.create_draft(
            session,
            user_id=users[1].id,
            context_type="knowledge_base",
            name="foreign",
            status="ready",
        )
        owned.subtask_id = 41
        foreign.subtask_id = 41

    with session_factory() as session:
        assert repository.list_for_subtasks(session, user_id=users[0].id, subtask_ids=[]) == []
        rows = repository.list_for_subtasks(
            session,
            user_id=users[0].id,
            subtask_ids=[41],
        )
        assert [row.id for row in rows] == [owned.id]
        assert session.scalars(select(models.SubtaskContext)).all()


def test_runtime_context_projection_excludes_binary_error_and_internal_columns(
    session_factory: sessionmaker[Session],
    users: tuple[_UserRef, _UserRef],
) -> None:
    repository = SubtaskContextRepository()
    with session_factory.begin() as session:
        context = repository.create_draft(
            session,
            user_id=users[0].id,
            context_type="attachment",
            name="private.txt",
            status="ready",
            binary_data=b"private binary",
            extracted_text="safe extracted text",
        )
        context.subtask_id = 7
        context_id = context.id

    statements: list[str] = []
    engine = session_factory.kw["bind"]

    def capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with session_factory() as session:
            rows = repository.list_runtime_for_subtasks(
                session,
                user_id=users[0].id,
                subtask_ids=[7],
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert [row.id for row in rows] == [context_id]
    assert [field.name for field in fields(SubtaskRuntimeContextProjection)] == [
        "id",
        "subtask_id",
        "context_type",
        "name",
        "image_base64",
        "extracted_text",
        "mime_type",
        "type_data",
    ]
    assert len(statements) == 1
    assert "binary_data" not in statements[0]
    assert "error_message" not in statements[0]
    assert "file_size" not in statements[0]
