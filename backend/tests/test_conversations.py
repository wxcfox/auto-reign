from collections.abc import Collection
from datetime import UTC, datetime
from inspect import signature
import json

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db import models
from app.repositories.attachment_repository import AttachmentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.attachments import AttachmentResponse
from app.schemas.conversations import (
    ConversationAgentResponse,
    ConversationDeleteResponse,
    ConversationDetailResponse,
    ConversationHistoryItemResponse,
    ConversationListResponse,
    ConversationMessageResponse,
    ConversationModelPutRequest,
    ConversationRenameRequest,
    ConversationSendRequest,
)
from app.schemas.modeling import ModelRef
from app.services.knowledge_document_service import KnowledgeDocumentService
from app.services.knowledge_vector_store import KnowledgeVectorHit
from app.services.model_service import ModelService
from app.services.runtime_types import ToolCall, ToolResult
from app.services.conversation_service import (
    ConversationService,
    conversation_message_response,
)


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _add_user(session: Session, user_id: int, username: str) -> models.User:
    user = models.User(
        id=user_id,
        username=username,
        password_hash="not-used",
        display_name=username.title(),
        role="user",
        is_active=True,
        token_version=1,
        settings_json={},
    )
    session.add(user)
    session.flush()
    return user


def _add_agent(
    session: Session,
    *,
    agent_id: str,
    owner_id: int,
    name: str,
    is_active: bool = True,
    deleted: bool = False,
) -> models.Resource:
    agent = models.Resource(
        id=agent_id,
        user_id=owner_id,
        resource_type="agent",
        name=name,
        config_json={
            "system_prompt": "Help.",
            "default_model": None,
            "home_workspace_id": None,
            "knowledge_scopes": [],
        },
        is_active=is_active,
        deleted_at=models._now() if deleted else None,
    )
    session.add(agent)
    session.flush()
    return agent


def _add_conversation(
    session: Session,
    *,
    user_id: int,
    agent_id: str,
    title: str = "Conversation",
    status: str = "idle",
    model_override: ModelRef | None = None,
    deleted: bool = False,
) -> models.Conversation:
    conversation = models.Conversation(
        user_id=user_id,
        agent_id=agent_id,
        title=title,
        status=status,
        model_override_json=(
            model_override.model_dump(mode="json")
            if model_override is not None
            else None
        ),
        deleted_at=models._now() if deleted else None,
    )
    session.add(conversation)
    session.flush()
    return conversation


def _add_message(
    session: Session,
    *,
    user_id: int,
    conversation_id: str,
    sequence: int,
    role: str,
    status: str,
    content: str,
    provider: str | None = None,
    model: str | None = None,
    metadata: dict[str, object] | None = None,
) -> models.Message:
    message = models.Message(
        user_id=user_id,
        conversation_id=conversation_id,
        sequence=sequence,
        role=role,
        status=status,
        content=content,
        provider=provider,
        model=model,
        metadata_json=metadata or {},
    )
    session.add(message)
    session.flush()
    return message


def _add_attachment(
    session: Session,
    *,
    attachment_id: str,
    user_id: int,
    message_id: str,
    filename: str,
) -> models.Attachment:
    attachment = models.Attachment(
        id=attachment_id,
        user_id=user_id,
        message_id=message_id,
        original_filename=filename,
        object_key=f"users/{user_id}/attachments/{attachment_id}/original",
        parsed_object_key=f"users/{user_id}/attachments/{attachment_id}/parsed",
        mime_type="text/plain",
        size_bytes=4,
        content_hash=f"sha256:{attachment_id}",
        parsed_size_bytes=4,
        parsed_content_hash=f"sha256:parsed-{attachment_id}",
    )
    session.add(attachment)
    session.flush()
    return attachment


def _agent_payload(
    name: str = "Conversation Agent",
    *,
    default_model: dict[str, str] | None = None,
    home_workspace_id: str | None = None,
    knowledge_scopes: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "name": name,
        "config": {
            "system_prompt": "Answer as the configured conversation Agent.",
            "default_model": default_model
            or {"provider": "qwen", "model": "qwen3.7-plus"},
            "home_workspace_id": home_workspace_id,
            "knowledge_scopes": knowledge_scopes or [],
        },
    }


def _create_api_agent(client, headers: dict[str, str], *, name: str = "Agent") -> dict:
    response = client.post(
        "/api/agents",
        headers=headers,
        json=_agent_payload(name),
    )
    assert response.status_code == 201
    return response.json()


def _parse_sse(response) -> list[tuple[str, dict[str, object]]]:
    assert response.status_code == 200
    parsed: list[tuple[str, dict[str, object]]] = []
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        event = next(
            line.removeprefix("event: ")
            for line in lines
            if line.startswith("event: ")
        )
        payload = "\n".join(
            line.removeprefix("data: ")
            for line in lines
            if line.startswith("data: ")
        )
        parsed.append((event, json.loads(payload)))
    return parsed


def _sse_error(response) -> dict[str, object]:
    events = _parse_sse(response)
    errors = [payload for event, payload in events if event == "error"]
    assert len(errors) == 1
    return errors[0]


def test_conversation_schemas_expose_only_the_unified_contract() -> None:
    assert set(ConversationMessageResponse.model_fields) == {
        "id",
        "role",
        "status",
        "content",
        "provider",
        "model",
        "created_at",
        "updated_at",
        "metadata",
        "attachments",
    }
    assert set(ConversationHistoryItemResponse.model_fields) == {
        "id",
        "title",
        "href",
        "agent",
        "model_override",
        "status",
        "started_at",
        "updated_at",
        "last_message",
    }
    assert set(ConversationDetailResponse.model_fields) == {
        *ConversationHistoryItemResponse.model_fields,
        "messages",
    }
    assert set(ConversationListResponse.model_fields) == {"conversations"}
    assert set(ConversationRenameRequest.model_fields) == {"title"}
    assert set(ConversationDeleteResponse.model_fields) == {"id", "status"}
    assert set(ConversationSendRequest.model_fields) == {
        "text",
        "conversation_id",
        "agent_id",
        "model_override",
        "attachment_ids",
    }
    assert set(ConversationModelPutRequest.model_fields) == {"model_override"}


def test_conversation_requests_validate_bounds_and_nullable_model_override() -> None:
    selected = ConversationModelPutRequest(
        model_override={"provider": "qwen", "model": "qwen3.7-plus"}
    )
    assert selected.model_override == ModelRef(
        provider="qwen", model="qwen3.7-plus"
    )
    assert ConversationModelPutRequest(model_override=None).model_override is None
    assert ConversationSendRequest(text="hello").model_override is None
    assert ConversationSendRequest(text="hello").attachment_ids == []
    with pytest.raises(ValidationError):
        ConversationSendRequest(text="")
    with pytest.raises(ValidationError):
        ConversationSendRequest(text="x" * 20_001)
    with pytest.raises(ValidationError, match="attachment ids must be unique"):
        ConversationSendRequest(text="hello", attachment_ids=["same", "same"])
    with pytest.raises(ValidationError):
        ConversationSendRequest(text="hello", attachment_ids=[""])
    with pytest.raises(ValidationError):
        ConversationSendRequest(text="hello", attachment_ids=["x" * 37])
    with pytest.raises(ValidationError):
        ConversationSendRequest(
            text="hello",
            attachment_ids=[f"attachment-{index}" for index in range(11)],
        )


def test_repository_has_only_the_new_single_track_public_surface() -> None:
    public_methods = {
        name
        for name in dir(ConversationRepository)
        if not name.startswith("_")
        and callable(getattr(ConversationRepository, name))
    }
    assert public_methods == {
        "append_pending_turn",
        "checkpoint_assistant",
        "create_generating",
        "finish_assistant",
        "get",
        "get_for_update",
        "list_messages",
        "list_model_history",
        "list_recent",
        "recover_interrupted",
        "rename",
        "set_model_override",
        "soft_delete",
    }
    assert "kind" not in signature(ConversationRepository.get).parameters


def test_list_recent_keeps_creation_order_after_conversation_activity(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(
        db_session,
        agent_id="agent-1",
        owner_id=1,
        name="General Agent",
    )
    older = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
        title="Older",
    )
    newer = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
        title="Newer",
    )
    older.created_at = datetime(2026, 7, 1, tzinfo=UTC)
    newer.created_at = datetime(2026, 7, 2, tzinfo=UTC)
    older.updated_at = datetime(2026, 7, 3, tzinfo=UTC)
    newer.updated_at = datetime(2026, 7, 2, tzinfo=UTC)
    db_session.flush()

    history = ConversationRepository().list_recent(
        db_session,
        user_id=1,
    )

    assert [item.conversation.id for item in history] == [newer.id, older.id]


def test_repository_creates_generating_conversation_with_fixed_agent(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(
        db_session,
        agent_id="agent-1",
        owner_id=1,
        name="Growth Agent",
    )
    override = ModelRef(provider="qwen", model="qwen3.7-max")

    conversation = ConversationRepository().create_generating(
        db_session,
        user_id=1,
        agent_id="agent-1",
        title="Practice",
        model_override=override,
    )

    assert conversation.user_id == 1
    assert conversation.agent_id == "agent-1"
    assert conversation.status == "generating"
    assert conversation.model_override_json == override.model_dump(mode="json")


def test_repository_get_and_lock_enforce_owner_and_tombstone(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Alice Agent")
    active = _add_conversation(db_session, user_id=1, agent_id="agent-1")
    deleted = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
        title="Deleted",
        deleted=True,
    )
    repository = ConversationRepository()

    assert repository.get(
        db_session, user_id=1, conversation_id=active.id
    ) is active
    assert repository.get(
        db_session, user_id=2, conversation_id=active.id
    ) is None
    assert repository.get(
        db_session, user_id=1, conversation_id=deleted.id
    ) is None
    assert repository.get_for_update(
        db_session, user_id=1, conversation_id=active.id
    ) is active
    assert repository.get_for_update(
        db_session, user_id=2, conversation_id=active.id
    ) is None


def test_repository_locks_conversation_and_appends_pending_turn(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
    )
    repository = ConversationRepository()
    locked = repository.get_for_update(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
    )
    assert locked is conversation

    user_message, assistant_message = repository.append_pending_turn(
        db_session,
        conversation=locked,
        text="请给我抽一道题",
        provider="qwen",
        model="qwen3.7-plus",
        metadata={"agent_config_hash": "abc"},
    )

    assert (user_message.sequence, assistant_message.sequence) == (1, 2)
    assert (user_message.role, user_message.status) == ("user", "completed")
    assert user_message.provider is None and user_message.model is None
    assert (assistant_message.role, assistant_message.status) == (
        "assistant",
        "pending",
    )
    assert assistant_message.provider == "qwen"
    assert assistant_message.model == "qwen3.7-plus"
    assert assistant_message.metadata_json == {"agent_config_hash": "abc"}
    assert locked.status == "generating"


def test_append_sequence_max_is_scoped_by_message_user_id(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session, user_id=1, agent_id="agent-1"
    )
    _add_message(
        db_session,
        user_id=2,
        conversation_id=conversation.id,
        sequence=99,
        role="assistant",
        status="completed",
        content="corrupt cross-user row",
    )

    user_message, assistant = ConversationRepository().append_pending_turn(
        db_session,
        conversation=conversation,
        text="accepted",
        provider="qwen",
        model="qwen3.7-plus",
        metadata={},
    )

    assert (user_message.sequence, assistant.sequence) == (1, 2)


def test_repository_lists_messages_by_sequence_and_user(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session, user_id=1, agent_id="agent-1"
    )
    _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=2,
        role="assistant",
        status="completed",
        content="second",
    )
    _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        status="completed",
        content="first",
    )
    _add_message(
        db_session,
        user_id=2,
        conversation_id=conversation.id,
        sequence=3,
        role="assistant",
        status="completed",
        content="other user",
    )

    messages = ConversationRepository().list_messages(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
    )

    assert [message.content for message in messages] == ["first", "second"]


def test_checkpoint_and_finish_assistant_are_owner_scoped(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
        status="generating",
    )
    user_message = _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        status="completed",
        content="question",
    )
    assistant = _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=2,
        role="assistant",
        status="pending",
        content="",
        metadata={"agent_config_hash": "abc"},
    )
    repository = ConversationRepository()

    with pytest.raises(ValueError, match="assistant_message_not_found"):
        repository.checkpoint_assistant(
            db_session,
            user_id=1,
            message_id=user_message.id,
            content="must not overwrite a user message",
        )
    with pytest.raises(ValueError, match="assistant_message_not_found"):
        repository.checkpoint_assistant(
            db_session,
            user_id=2,
            message_id=assistant.id,
            content="leak",
        )
    checkpoint = repository.checkpoint_assistant(
        db_session,
        user_id=1,
        message_id=assistant.id,
        content="partial",
    )
    assert checkpoint.content == "partial"
    assert checkpoint.status == "streaming"

    finished = repository.finish_assistant(
        db_session,
        user_id=1,
        message_id=assistant.id,
        content="partial response",
        status="failed",
        error_code="provider_call_failed",
    )
    assert finished.status == "failed"
    assert finished.metadata_json == {
        "agent_config_hash": "abc",
        "error_code": "provider_call_failed",
    }
    assert conversation.status == "idle"


def test_model_history_excludes_failed_and_incomplete_messages(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session, user_id=1, agent_id="agent-1"
    )
    rows = [
        (1, "user", "completed", "accepted user input"),
        (2, "assistant", "completed", "completed reply"),
        (3, "user", "completed", "second accepted input"),
        (4, "assistant", "failed", "failed partial"),
        (5, "assistant", "streaming", "streaming partial"),
        (6, "system", "completed", "legacy system row"),
    ]
    for sequence, role, status, content in rows:
        _add_message(
            db_session,
            user_id=1,
            conversation_id=conversation.id,
            sequence=sequence,
            role=role,
            status=status,
            content=content,
        )

    result = ConversationRepository().list_model_history(
        db_session,
        user_id=conversation.user_id,
        conversation_id=conversation.id,
    )

    assert [item.content for item in result] == [
        "accepted user input",
        "completed reply",
        "second accepted input",
    ]


def test_model_history_limit_keeps_most_recent_completed_messages_in_order(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session, user_id=1, agent_id="agent-1"
    )
    for sequence in range(1, 6):
        _add_message(
            db_session,
            user_id=1,
            conversation_id=conversation.id,
            sequence=sequence,
            role="user" if sequence % 2 else "assistant",
            status="completed",
            content=str(sequence),
        )

    result = ConversationRepository().list_model_history(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        max_messages=3,
    )

    assert [item.content for item in result] == ["3", "4", "5"]


def test_repository_updates_model_rename_and_soft_delete_without_changing_agent(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session, user_id=1, agent_id="agent-1"
    )
    repository = ConversationRepository()
    override = ModelRef(provider="qwen", model="qwen3.7-max")

    repository.set_model_override(
        db_session,
        conversation=conversation,
        model_override=override,
    )
    assert conversation.model_override_json == override.model_dump(mode="json")
    repository.set_model_override(
        db_session,
        conversation=conversation,
        model_override=None,
    )
    assert conversation.model_override_json is None
    assert repository.rename(
        db_session,
        user_id=2,
        conversation_id=conversation.id,
        title="cross-user",
    ) is None
    assert repository.rename(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        title="Renamed",
    ) is conversation
    assert conversation.title == "Renamed"
    assert conversation.agent_id == "agent-1"
    assert repository.soft_delete(
        db_session,
        user_id=2,
        conversation_id=conversation.id,
    ) is False
    assert repository.soft_delete(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
    ) is True
    assert conversation.deleted_at is not None


def test_recover_interrupted_is_the_global_startup_exception(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    _add_agent(db_session, agent_id="global-agent", owner_id=0, name="Global")
    first = _add_conversation(
        db_session,
        user_id=1,
        agent_id="global-agent",
        status="generating",
    )
    second = _add_conversation(
        db_session,
        user_id=2,
        agent_id="global-agent",
        status="generating",
    )
    pending = _add_message(
        db_session,
        user_id=1,
        conversation_id=first.id,
        sequence=1,
        role="assistant",
        status="pending",
        content="",
        metadata={"kept": True},
    )
    streaming = _add_message(
        db_session,
        user_id=2,
        conversation_id=second.id,
        sequence=1,
        role="assistant",
        status="streaming",
        content="partial",
    )
    completed = _add_message(
        db_session,
        user_id=1,
        conversation_id=first.id,
        sequence=2,
        role="assistant",
        status="completed",
        content="done",
    )

    recovered = ConversationRepository().recover_interrupted(db_session)

    assert recovered == 2
    assert pending.status == streaming.status == "failed"
    assert pending.metadata_json == {
        "kept": True,
        "error_code": "generation_interrupted",
    }
    assert streaming.metadata_json["error_code"] == "generation_interrupted"
    assert completed.status == "completed"
    assert first.status == second.status == "idle"
    assert ConversationRepository().recover_interrupted(db_session) == 0


def test_conversation_message_response_maps_metadata_json() -> None:
    message = models.Message(
        id="message-1",
        user_id=1,
        conversation_id="conversation-1",
        sequence=1,
        role="assistant",
        status="failed",
        content="partial",
        provider="qwen",
        model="qwen3.7-plus",
        metadata_json={"error_code": "provider_call_failed"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )

    response = conversation_message_response(message)

    assert response.metadata == {"error_code": "provider_call_failed"}
    assert response.attachments == []
    assert "metadata_json" not in response.model_dump()


def test_service_bulk_projects_public_attachments_without_storage_metadata(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(
        db_session,
        agent_id="agent-1",
        owner_id=1,
        name="Attachment Agent",
    )
    conversation = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
    )
    first = _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        status="completed",
        content="first",
    )
    second = _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=2,
        role="assistant",
        status="completed",
        content="second",
    )
    attachment = _add_attachment(
        db_session,
        attachment_id="attachment-1",
        user_id=1,
        message_id=first.id,
        filename="source.txt",
    )

    class RecordingAttachmentRepository(AttachmentRepository):
        def __init__(self) -> None:
            self.calls: list[tuple[int, tuple[str, ...]]] = []

        def list_for_messages(
            self,
            session: Session,
            *,
            user_id: int,
            message_ids: Collection[str],
        ) -> list[models.Attachment]:
            self.calls.append((user_id, tuple(message_ids)))
            return super().list_for_messages(
                session,
                user_id=user_id,
                message_ids=message_ids,
            )

    attachments = RecordingAttachmentRepository()
    detail = ConversationService(
        attachment_repository=attachments,
    ).get_conversation(
        db_session,
        conversation.id,
        user_id=1,
    )

    assert detail is not None
    assert attachments.calls == [(1, (first.id, second.id))]
    assert detail.messages[0].attachments == [
        AttachmentResponse(
            id=attachment.id,
            filename="source.txt",
            mime_type="text/plain",
            size_bytes=4,
            message_id=first.id,
            created_at=attachment.created_at,
        )
    ]
    assert detail.messages[1].attachments == []
    public = detail.messages[0].attachments[0].model_dump()
    assert set(public) == {
        "id",
        "filename",
        "mime_type",
        "size_bytes",
        "message_id",
        "created_at",
    }
    assert "object_key" not in public
    assert "parsed_object_key" not in public
    assert "content_hash" not in public


def test_service_projects_unified_history_and_failed_partial(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(
        db_session,
        agent_id="agent-1",
        owner_id=1,
        name="Growth Agent",
    )
    override = ModelRef(provider="qwen", model="qwen3.7-max")
    conversation = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
        title="  Practice  ",
        model_override=override,
    )
    _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        status="completed",
        content="question",
    )
    failed = _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=2,
        role="assistant",
        status="failed",
        content="failed partial response",
        provider="qwen",
        model="qwen3.7-max",
        metadata={"error_code": "provider_call_failed"},
    )
    service = ConversationService()

    history = service.list_conversations(db_session, user_id=1)
    detail = service.get_conversation(
        db_session,
        conversation.id,
        user_id=1,
    )

    assert len(history) == 1
    item = history[0]
    assert item.title == "Practice"
    assert item.href == f"/chat?session={conversation.id}"
    assert item.agent == ConversationAgentResponse(
        id="agent-1",
        name="Growth Agent",
        is_available=True,
    )
    assert item.model_override == override
    assert item.last_message == "failed partial response"
    assert detail is not None
    assert detail.messages[-1] == conversation_message_response(failed)


def test_agent_tombstone_keeps_history_name_but_is_unavailable(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(
        db_session,
        agent_id="agent-1",
        owner_id=1,
        name="Archived Growth Agent",
        is_active=False,
        deleted=True,
    )
    conversation = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
        title="History",
    )

    detail = ConversationService().get_conversation(
        db_session,
        conversation.id,
        user_id=1,
    )

    assert detail is not None
    assert detail.agent == ConversationAgentResponse(
        id="agent-1",
        name="Archived Growth Agent",
        is_available=False,
    )
    assert detail.href == f"/chat?session={conversation.id}"


def test_service_last_message_skips_empty_pending_assistant(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_agent(db_session, agent_id="agent-1", owner_id=1, name="Agent")
    conversation = _add_conversation(
        db_session,
        user_id=1,
        agent_id="agent-1",
    )
    _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=1,
        role="user",
        status="completed",
        content="latest user question",
    )
    _add_message(
        db_session,
        user_id=1,
        conversation_id=conversation.id,
        sequence=2,
        role="assistant",
        status="pending",
        content="",
    )

    history = ConversationService().list_conversations(db_session, user_id=1)

    assert history[0].last_message == "latest user question"


def test_service_lists_many_conversations_in_two_queries_without_loading_messages(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    active_agent = _add_agent(
        db_session,
        agent_id="agent-active",
        owner_id=1,
        name="Active Agent",
    )
    archived_agent = _add_agent(
        db_session,
        agent_id="agent-archived",
        owner_id=1,
        name="Archived Agent",
        is_active=False,
        deleted=True,
    )
    first = _add_conversation(
        db_session,
        user_id=1,
        agent_id=active_agent.id,
        title="First",
    )
    second = _add_conversation(
        db_session,
        user_id=1,
        agent_id=archived_agent.id,
        title="Second",
    )
    messages = [
        models.Message(
            user_id=1,
            conversation_id=first.id,
            sequence=1,
            role="user",
            status="completed",
            content="first visible message",
            metadata_json={},
        ),
        models.Message(
            user_id=1,
            conversation_id=first.id,
            sequence=2,
            role="assistant",
            status="completed",
            content="   ",
            metadata_json={},
        ),
        models.Message(
            user_id=1,
            conversation_id=first.id,
            sequence=3,
            role="assistant",
            status="completed",
            content="\t\n\r",
            metadata_json={},
        ),
        models.Message(
            user_id=1,
            conversation_id=first.id,
            sequence=4,
            role="assistant",
            status="pending",
            content="",
            metadata_json={},
        ),
    ]
    messages.extend(
        models.Message(
            user_id=1,
            conversation_id=second.id,
            sequence=sequence,
            role="user" if sequence % 2 else "assistant",
            status="completed",
            content=f"historical message {sequence}",
            metadata_json={},
        )
        for sequence in range(1, 101)
    )
    messages.extend(
        [
            models.Message(
                user_id=1,
                conversation_id=second.id,
                sequence=101,
                role="assistant",
                status="failed",
                content="failed partial response",
                metadata_json={},
            ),
            models.Message(
                user_id=1,
                conversation_id=second.id,
                sequence=102,
                role="assistant",
                status="pending",
                content="",
                metadata_json={},
            ),
        ]
    )
    db_session.add_all(messages)
    db_session.flush()
    first_id = first.id
    second_id = second.id
    db_session.expunge_all()

    statements: list[str] = []
    loaded_messages = 0

    def record_statement(*args: object) -> None:
        statements.append(str(args[2]))

    def record_message_load(*_args: object) -> None:
        nonlocal loaded_messages
        loaded_messages += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", record_statement)
    event.listen(models.Message, "load", record_message_load)
    try:
        history = ConversationService().list_conversations(
            db_session,
            user_id=1,
        )
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)
        event.remove(models.Message, "load", record_message_load)

    by_id = {item.id: item for item in history}
    assert len(statements) == 2
    assert loaded_messages == 0
    assert by_id[first_id].last_message == "first visible message"
    assert by_id[first_id].agent == ConversationAgentResponse(
        id="agent-active",
        name="Active Agent",
        is_available=True,
    )
    assert by_id[second_id].last_message == "failed partial response"
    assert by_id[second_id].agent == ConversationAgentResponse(
        id="agent-archived",
        name="Archived Agent",
        is_available=False,
    )


def test_all_history_items_use_chat_href_and_have_no_kind(
    client,
    ordinary_user_headers,
) -> None:
    with client.app.state.session_factory() as session:
        user = session.scalar(select(models.User).where(models.User.username == "alice"))
        assert user is not None
        agent = models.Resource(
            user_id=user.id,
            resource_type="agent",
            name="Alice Agent",
            config_json={
                "system_prompt": "Help.",
                "default_model": None,
                "home_workspace_id": None,
                "knowledge_scopes": [],
            },
        )
        session.add(agent)
        session.flush()
        first = models.Conversation(
            user_id=user.id,
            agent_id=agent.id,
            title="First",
            status="idle",
        )
        second = models.Conversation(
            user_id=user.id,
            agent_id=agent.id,
            title="Second",
            status="idle",
        )
        session.add_all([first, second])
        session.commit()

    response = client.get(
        "/api/conversations",
        headers=ordinary_user_headers,
    )

    assert response.status_code == 200
    items = response.json()["conversations"]
    assert {item["id"] for item in items} == {first.id, second.id}
    assert all(item["href"] == f"/chat?session={item['id']}" for item in items)
    assert all("kind" not in item for item in items)
    assert all(item["agent"]["name"] == "Alice Agent" for item in items)


def test_conversation_api_detail_rename_and_delete_use_new_responses(
    client,
    ordinary_user_headers,
) -> None:
    with client.app.state.session_factory() as session:
        user = session.scalar(select(models.User).where(models.User.username == "alice"))
        assert user is not None
        agent = models.Resource(
            user_id=user.id,
            resource_type="agent",
            name="Alice Agent",
            config_json={
                "system_prompt": "Help.",
                "default_model": None,
                "home_workspace_id": None,
                "knowledge_scopes": [],
            },
        )
        session.add(agent)
        session.flush()
        conversation = models.Conversation(
            user_id=user.id,
            agent_id=agent.id,
            title="Original",
            status="idle",
        )
        session.add(conversation)
        session.flush()
        message = models.Message(
            user_id=user.id,
            conversation_id=conversation.id,
            sequence=1,
            role="assistant",
            status="failed",
            content="partial",
            provider="qwen",
            model="qwen3.7-plus",
            metadata_json={"error_code": "provider_call_failed"},
        )
        session.add(message)
        session.commit()
        conversation_id = conversation.id

    detail = client.get(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["messages"][0]["metadata"] == {
        "error_code": "provider_call_failed"
    }
    assert "message_type" not in detail.json()["messages"][0]

    renamed = client.patch(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
        json={"title": "  Renamed  "},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed"
    assert renamed.json()["href"] == f"/chat?session={conversation_id}"

    deleted = client.delete(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"id": conversation_id, "status": "deleted"}
    assert client.get(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
    ).status_code == 404


def test_unified_stream_creates_and_continues_conversation(
    client,
    ordinary_user_headers,
) -> None:
    agent = _create_api_agent(client, ordinary_user_headers)

    first_response = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={
            "text": "Give me one practice question.",
            "agent_id": agent["id"],
            "model_override": None,
        },
    )
    assert first_response.headers["content-type"].startswith("text/event-stream")
    assert first_response.headers["cache-control"] == "no-cache"
    assert first_response.headers["x-accel-buffering"] == "no"
    first_events = _parse_sse(first_response)

    assert first_events[0][0] == "accepted"
    assert first_events[0][1]["attachment_ids"] == []
    assert isinstance(first_events[0][1]["conversation_id"], str)
    assert isinstance(first_events[0][1]["user_message_id"], str)
    assert isinstance(first_events[0][1]["assistant_message_id"], str)
    assert first_events[-1][0] == "result"
    first_result = first_events[-1][1]
    conversation_id = first_result["conversation_id"]
    assert isinstance(conversation_id, str)
    assert first_result["message"]["status"] == "completed"  # type: ignore[index]

    second_response = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={
            "text": "Now explain the answer.",
            "conversation_id": conversation_id,
        },
    )
    second_events = _parse_sse(second_response)

    assert second_events[-1][0] == "result"
    assert second_events[-1][1]["conversation_id"] == conversation_id
    detail = client.get(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
    )
    assert detail.status_code == 200
    assert [
        (message["role"], message["status"])
        for message in detail.json()["messages"]
    ] == [
        ("user", "completed"),
        ("assistant", "completed"),
        ("user", "completed"),
        ("assistant", "completed"),
    ]


def test_real_sse_uses_one_runtime_for_agent_home_and_attachment(
    client,
    ordinary_user_headers,
) -> None:
    class RecordingModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def stream_turn(
            self,
            messages,
            *,
            provider: str,
            model: str,
            call_index: int,
            observer,
            tools=None,
        ):
            del observer
            self.calls.append(
                {
                    "messages": messages,
                    "provider": provider,
                    "model": model,
                    "call_index": call_index,
                    "tools": tools,
                }
            )
            return iter(("combined answer",))

    workspace = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json={
            "name": "Learning Home",
            "config": {
                "workspace_type": "agent_home",
                "initial_agents_md": "# Initial Home instructions",
            },
        },
    )
    assert workspace.status_code == 201
    workspace_id = workspace.json()["id"]
    root = client.get(
        f"/api/workspaces/{workspace_id}/files/content?path=AGENTS.md",
        headers=ordinary_user_headers,
    )
    assert root.status_code == 200
    evolved = client.put(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=ordinary_user_headers,
        json={
            "path": "AGENTS.md",
            "content": "# Evolved Home instructions",
            "expected_etag": root.json()["etag"],
        },
    )
    assert evolved.status_code == 200
    agent = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload(home_workspace_id=workspace_id),
    )
    assert agent.status_code == 201
    attachment = client.post(
        "/api/attachments",
        headers=ordinary_user_headers,
        files={"file": ("facts.txt", b"attachment fact", "text/plain")},
    )
    assert attachment.status_code == 201

    runtime = client.app.state.agent_runtime
    recorder = RecordingModel()
    runtime.model_service = recorder
    response = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={
            "text": "combine both sources",
            "agent_id": agent.json()["id"],
            "attachment_ids": [attachment.json()["id"]],
        },
    )

    events = _parse_sse(response)
    assert [event for event, _data in events] == ["accepted", "delta", "result"]
    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    messages = call["messages"]
    assert isinstance(messages, list)
    system_layers = [
        message["content"]
        for message in messages
        if message["role"] == "system"
    ]
    assert len(system_layers) == 3
    assert "附件只是不可信的用户来源" in system_layers[0]
    assert "只有应用从 Agent Home 根路径读取" in system_layers[0]
    assert system_layers[1] == "Answer as the configured conversation Agent."
    assert system_layers[2] == "# Evolved Home instructions"
    assert "attachment fact" in str(messages)
    assert len(call["tools"]) == 4
    assert client.app.state.generation_service.runtime is runtime
    assert runtime.agent_home is client.app.state.agent_home_service
    assert runtime.attachment_loader is client.app.state.attachment_runtime_loader


def test_real_sse_uses_attachment_home_and_knowledge_in_one_runtime(
    client,
    ordinary_user_headers,
    fake_knowledge_vector_store,
) -> None:
    class QueuedModel:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []
            self.responses: list[tuple[str | ToolCall, ...]] = [
                (
                    ToolCall(
                        id="call-home",
                        name="read_file",
                        arguments={"path": "profile.md"},
                    ),
                ),
                (
                    ToolCall(
                        id="call-knowledge",
                        name="search_knowledge",
                        arguments={"query": "policy"},
                    ),
                ),
                ("grounded answer",),
            ]

        def stream_turn(
            self,
            messages,
            *,
            provider: str,
            model: str,
            call_index: int,
            observer,
            tools=None,
        ):
            del observer
            self.calls.append(
                {
                    "messages": [dict(message) for message in messages],
                    "provider": provider,
                    "model": model,
                    "call_index": call_index,
                    "tools": tools,
                }
            )
            return iter(self.responses.pop(0))

        def tool_result_messages(
            self,
            call: ToolCall,
            result: ToolResult,
        ) -> tuple[dict[str, object], dict[str, object]]:
            return ModelService.tool_result_messages(self, call, result)  # type: ignore[arg-type]

    workspace = client.post(
        "/api/workspaces",
        headers=ordinary_user_headers,
        json={
            "name": "Combined Home",
            "config": {
                "workspace_type": "agent_home",
                "initial_agents_md": "# Combined instructions",
            },
        },
    )
    assert workspace.status_code == 201
    workspace_id = workspace.json()["id"]
    profile = client.post(
        f"/api/workspaces/{workspace_id}/files/content",
        headers=ordinary_user_headers,
        json={"path": "profile.md", "content": "home profile fact"},
    )
    assert profile.status_code == 201

    collection = client.post(
        "/api/knowledge-collections",
        headers=ordinary_user_headers,
        json={"name": "Runtime Policies", "config": {}},
    )
    assert collection.status_code == 201
    collection_id = collection.json()["id"]
    document_id = "document-runtime-1"
    generation = 3
    content_hash = "sha256-runtime-policy"
    filename = "policy.md"
    source = ("large preface " * 4_000) + "authoritative policy fact"
    with client.app.state.session_factory() as session:
        user = session.scalar(select(models.User).where(models.User.username == "alice"))
        assert user is not None
        owner_id = user.id
        parsed_key = KnowledgeDocumentService.parsed_key(
            owner_id,
            collection_id,
            document_id,
            generation,
        )
        document = models.KnowledgeDocument(
            id=document_id,
            user_id=owner_id,
            collection_id=collection_id,
            name=filename,
            source_object_key=KnowledgeDocumentService.source_key(
                owner_id,
                collection_id,
                document_id,
            ),
            parsed_object_key=parsed_key,
            mime_type="text/markdown",
            size_bytes=len(source.encode("utf-8")),
            content_hash=content_hash,
            status="ready",
            index_generation=generation,
            is_active=True,
            indexed_at=models._now(),
        )
        session.add(document)
        session.commit()
    client.app.state.object_store.put(
        parsed_key,
        source.encode("utf-8"),
        if_none_match=True,
    )
    chunk = "authoritative policy fact"
    start = source.index(chunk)
    fake_knowledge_vector_store.search_results = [
        KnowledgeVectorHit(
            content=chunk,
            score=0.93,
            metadata={
                "collection_id": collection_id,
                "owner_user_id": owner_id,
                "document_id": document_id,
                "index_generation": generation,
                "content_hash": content_hash,
                "filename": filename,
                "chunk_index": 7,
                "source_start": start,
                "source_end": start + len(chunk),
            },
        )
    ]

    agent = client.post(
        "/api/agents",
        headers=ordinary_user_headers,
        json=_agent_payload(
            home_workspace_id=workspace_id,
            knowledge_scopes=[
                {"collection_id": collection_id, "document_ids": None}
            ],
        ),
    )
    assert agent.status_code == 201
    attachment = client.post(
        "/api/attachments",
        headers=ordinary_user_headers,
        files={"file": ("context.txt", b"attachment fact", "text/plain")},
    )
    assert attachment.status_code == 201

    runtime = client.app.state.agent_runtime
    model = QueuedModel()
    runtime.model_service = model
    response = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={
            "text": "combine every source",
            "agent_id": agent.json()["id"],
            "attachment_ids": [attachment.json()["id"]],
        },
    )

    events = _parse_sse(response)
    assert [event for event, _data in events] == ["accepted", "delta", "result"]
    assert events[-1][1]["message"]["content"] == "grounded answer"  # type: ignore[index]
    assert len(model.calls) == 3
    assert "attachment fact" in str(model.calls[0]["messages"])
    assert "home profile fact" in str(model.calls[1]["messages"])
    assert chunk in str(model.calls[2]["messages"])
    assert [definition.name for definition in model.calls[0]["tools"]] == [
        "list_files",
        "read_file",
        "create_file",
        "write_file",
        "search_knowledge",
    ]
    assert fake_knowledge_vector_store.search_calls
    assert client.app.state.generation_service.runtime is runtime
    tool_calls = events[-1][1]["message"]["metadata"]["tool_calls"]  # type: ignore[index]
    assert [item["tool"] for item in tool_calls] == [
        "read_file",
        "search_knowledge",
    ]
    knowledge_audit = tool_calls[1]
    assert knowledge_audit["mode"] == "rag"
    assert knowledge_audit["sources"] == [
        {
            "document_id": document_id,
            "collection_id": collection_id,
            "filename": filename,
            "index_generation": generation,
            "content_hash": content_hash,
            "chunk_index": 7,
            "score": 0.93,
        }
    ]
    assert chunk not in str(knowledge_audit)


def test_stream_closes_auth_session_before_generation_body_is_iterated(
    client,
    ordinary_user_headers,
) -> None:
    agent = _create_api_agent(client, ordinary_user_headers)
    original_factory = client.app.state.session_factory
    active_auth_sessions = 0

    def tracking_factory():
        nonlocal active_auth_sessions
        session = original_factory()
        original_close = session.close
        closed = False
        active_auth_sessions += 1

        def close() -> None:
            nonlocal active_auth_sessions, closed
            if not closed:
                closed = True
                active_auth_sessions -= 1
            original_close()

        session.close = close
        return session

    class InspectingRuntime:
        def prepare_turn(self, turn):
            return turn

        def stream_turn(self, _turn, *, observer):
            del observer
            assert active_auth_sessions == 0
            return iter(("answer",))

    client.app.state.session_factory = tracking_factory
    client.app.state.generation_service.runtime = InspectingRuntime()

    response = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "start", "agent_id": agent["id"]},
    )

    assert _parse_sse(response)[-1][0] == "result"
    assert active_auth_sessions == 0


def test_model_override_can_set_clear_and_reject_invalid_or_active_generation(
    client,
    ordinary_user_headers,
) -> None:
    class RecordingRuntime:
        def __init__(self) -> None:
            self.models: list[tuple[str, str]] = []

        def prepare_turn(self, turn):
            return turn

        def stream_turn(self, turn, *, observer):
            del observer
            self.models.append((turn.provider, turn.model))
            return iter(("answer",))

    runtime = RecordingRuntime()
    client.app.state.generation_service.runtime = runtime
    agent = _create_api_agent(client, ordinary_user_headers)
    first = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={
            "text": "start",
            "agent_id": agent["id"],
            "model_override": {
                "provider": "openai",
                "model": "gpt-4.1",
            },
        },
    )
    conversation_id = _parse_sse(first)[-1][1]["conversation_id"]
    persisted = client.get(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
    )
    assert persisted.json()["model_override"] == {
        "provider": "openai",
        "model": "gpt-4.1",
    }

    selected = client.put(
        f"/api/conversations/{conversation_id}/model",
        headers=ordinary_user_headers,
        json={
            "model_override": {
                "provider": "qwen",
                "model": "qwen3.7-max",
            }
        },
    )
    assert selected.status_code == 200
    assert selected.json()["model_override"] == {
        "provider": "qwen",
        "model": "qwen3.7-max",
    }
    assert [message["content"] for message in selected.json()["messages"]] == [
        "start",
        "answer",
    ]
    openapi_response = client.get("/openapi.json").json()["paths"][
        "/api/conversations/{conversation_id}/model"
    ]["put"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert openapi_response["$ref"] == (
        "#/components/schemas/ConversationDetailResponse"
    )
    selected_turn = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "selected", "conversation_id": conversation_id},
    )
    selected_result = _parse_sse(selected_turn)[-1]
    assert selected_result[0] == "result"
    assert selected_result[1]["message"]["provider"] == "qwen"  # type: ignore[index]
    assert selected_result[1]["message"]["model"] == "qwen3.7-max"  # type: ignore[index]

    cleared = client.put(
        f"/api/conversations/{conversation_id}/model",
        headers=ordinary_user_headers,
        json={"model_override": None},
    )
    assert cleared.status_code == 200
    assert cleared.json()["model_override"] is None
    assert len(cleared.json()["messages"]) == 4
    default_turn = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "default", "conversation_id": conversation_id},
    )
    default_result = _parse_sse(default_turn)[-1]
    assert default_result[0] == "result"
    assert default_result[1]["message"]["provider"] == "qwen"  # type: ignore[index]
    assert default_result[1]["message"]["model"] == "qwen3.7-plus"  # type: ignore[index]
    assert runtime.models == [
        ("openai", "gpt-4.1"),
        ("qwen", "qwen3.7-max"),
        ("qwen", "qwen3.7-plus"),
    ]

    invalid = client.put(
        f"/api/conversations/{conversation_id}/model",
        headers=ordinary_user_headers,
        json={
            "model_override": {
                "provider": "qwen",
                "model": "not-configured",
            }
        },
    )
    assert invalid.status_code == 503
    assert invalid.json()["detail"]["code"] == "model_unavailable"

    with client.app.state.session_factory() as session:
        conversation = session.get(models.Conversation, conversation_id)
        assert conversation is not None
        conversation.status = "generating"
        session.commit()
    active = client.put(
        f"/api/conversations/{conversation_id}/model",
        headers=ordinary_user_headers,
        json={"model_override": None},
    )
    assert active.status_code == 409
    assert active.json()["detail"]["code"] == "generation_in_progress"


def test_model_override_is_tenant_scoped(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    agent = _create_api_agent(client, alice_headers)
    with client.app.state.session_factory() as session:
        conversation = models.Conversation(
            user_id=alice["id"],
            agent_id=agent["id"],
            title="Alice only",
            status="idle",
        )
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id

    response = client.put(
        f"/api/conversations/{conversation_id}/model",
        headers=bob_headers,
        json={"model_override": None},
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "conversation_not_found"


def test_clearing_model_override_validates_the_live_agent_default(
    client,
    ordinary_user_headers,
) -> None:
    agent = _create_api_agent(client, ordinary_user_headers)
    first = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={
            "text": "start",
            "agent_id": agent["id"],
            "model_override": {
                "provider": "qwen",
                "model": "qwen3.7-max",
            },
        },
    )
    conversation_id = _parse_sse(first)[-1][1]["conversation_id"]
    client.app.state.settings = client.app.state.settings.model_copy(
        update={
            "openai_api_key": None,
            "deepseek_api_key": None,
            "qwen_api_key": None,
        }
    )

    response = client.put(
        f"/api/conversations/{conversation_id}/model",
        headers=ordinary_user_headers,
        json={"model_override": None},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "model_unavailable"
    with client.app.state.session_factory() as session:
        conversation = session.get(models.Conversation, conversation_id)
        assert conversation is not None
        assert conversation.model_override_json == {
            "provider": "qwen",
            "model": "qwen3.7-max",
        }


def test_deleted_agent_history_is_readable_but_later_stream_is_blocked(
    client,
    ordinary_user_headers,
) -> None:
    agent = _create_api_agent(client, ordinary_user_headers, name="Archived Agent")
    first = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "remember this", "agent_id": agent["id"]},
    )
    conversation_id = _parse_sse(first)[-1][1]["conversation_id"]
    deleted = client.delete(
        f"/api/agents/{agent['id']}",
        headers=ordinary_user_headers,
    )
    assert deleted.status_code == 200

    detail = client.get(
        f"/api/conversations/{conversation_id}",
        headers=ordinary_user_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["agent"] == {
        "id": agent["id"],
        "name": "Archived Agent",
        "is_available": False,
    }

    blocked = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "continue", "conversation_id": conversation_id},
    )
    assert _sse_error(blocked) == {
        "code": "agent_unavailable",
        "message": "Agent is unavailable.",
        "status_code": 409,
    }


def test_retry_stream_succeeds_and_validates_message_and_tenant(
    client,
    create_user,
) -> None:
    alice, alice_headers = create_user("alice")
    _bob, bob_headers = create_user("bob")
    agent = _create_api_agent(client, alice_headers)
    with client.app.state.session_factory() as session:
        conversation = models.Conversation(
            user_id=alice["id"],
            agent_id=agent["id"],
            title="Retry",
            status="idle",
        )
        session.add(conversation)
        session.flush()
        user_message = models.Message(
            user_id=alice["id"],
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            status="completed",
            content="question",
            metadata_json={},
        )
        failed = models.Message(
            user_id=alice["id"],
            conversation_id=conversation.id,
            sequence=2,
            role="assistant",
            status="failed",
            content="partial",
            provider="qwen",
            model="qwen3.7-plus",
            metadata_json={"error_code": "provider_call_failed"},
        )
        session.add_all([user_message, failed])
        session.commit()
        conversation_id = conversation.id
        failed_id = failed.id
        user_message_id = user_message.id

    retried = client.post(
        f"/api/conversations/{conversation_id}/messages/{failed_id}/retry/stream",
        headers=alice_headers,
    )
    retried_events = _parse_sse(retried)
    assert retried_events[0][0] == "accepted"
    assert retried_events[0][1] == {
        "conversation_id": conversation_id,
        "user_message_id": None,
        "assistant_message_id": retried_events[0][1]["assistant_message_id"],
        "attachment_ids": [],
    }
    assert retried_events[-1][0] == "result"
    assert retried_events[-1][1]["message"]["metadata"][  # type: ignore[index]
        "retry_of_message_id"
    ] == failed_id

    invalid_message = client.post(
        f"/api/conversations/{conversation_id}/messages/{user_message_id}/retry/stream",
        headers=alice_headers,
    )
    assert _sse_error(invalid_message)["code"] == "message_not_found"

    wrong_tenant = client.post(
        f"/api/conversations/{conversation_id}/messages/{failed_id}/retry/stream",
        headers=bob_headers,
    )
    assert _sse_error(wrong_tenant)["code"] == "conversation_not_found"


def test_stream_error_protocol_preserves_http_error_and_hides_unknown_failure(
    client,
    ordinary_user_headers,
) -> None:
    from fastapi import HTTPException
    from app.services.agent_runtime import RuntimeTerminalError

    class FailingRuntime:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def prepare_turn(self, turn):
            return turn

        def stream_turn(self, _turn, *, observer):
            del observer
            raise self.error

    validation_failure = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "first"},
    )
    assert _sse_error(validation_failure) == {
        "code": "agent_required",
        "message": "Agent is required for a new conversation.",
        "status_code": 400,
    }

    agent = _create_api_agent(client, ordinary_user_headers)
    service = client.app.state.generation_service
    service.runtime = FailingRuntime(
        HTTPException(
            status_code=503,
            detail={
                "code": "knowledge_unavailable",
                "message": "Knowledge is temporarily unavailable.",
            },
        )
    )
    http_failure = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "first", "agent_id": agent["id"]},
    )
    http_events = _parse_sse(http_failure)
    assert [event for event, _payload in http_events] == ["accepted", "error"]
    http_error = _sse_error(http_failure)
    assert {
        key: http_error[key]
        for key in ("code", "message", "status_code")
    } == {
        "code": "knowledge_unavailable",
        "message": "Knowledge is temporarily unavailable.",
        "status_code": 503,
    }
    assert isinstance(http_error["conversation_id"], str)
    assert isinstance(http_error["assistant_message_id"], str)
    http_detail = client.get(
        f"/api/conversations/{http_error['conversation_id']}",
        headers=ordinary_user_headers,
    ).json()
    assert http_detail["messages"][-1]["id"] == http_error["assistant_message_id"]
    assert http_detail["messages"][-1]["status"] == "failed"

    service.runtime = FailingRuntime(
        RuntimeTerminalError(
            code="workspace_unavailable",
            message="The workspace is temporarily unavailable.",
            status_code=503,
        )
    )
    terminal_failure = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "read home", "agent_id": agent["id"]},
    )
    terminal_events = _parse_sse(terminal_failure)
    assert [event for event, _payload in terminal_events] == ["accepted", "error"]
    terminal_error = terminal_events[-1][1]
    assert {
        key: terminal_error[key]
        for key in ("code", "message", "status_code")
    } == {
        "code": "workspace_unavailable",
        "message": "The workspace is temporarily unavailable.",
        "status_code": 503,
    }
    terminal_detail = client.get(
        f"/api/conversations/{terminal_error['conversation_id']}",
        headers=ordinary_user_headers,
    ).json()
    assert terminal_detail["messages"][-1]["status"] == "failed"
    assert terminal_detail["messages"][-1]["metadata"]["error_code"] == (
        "workspace_unavailable"
    )

    private_detail = "private provider payload must not leak"
    service.runtime = FailingRuntime(RuntimeError(private_detail))
    unknown_failure = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "second", "agent_id": agent["id"]},
    )
    unknown_error = _sse_error(unknown_failure)
    assert {
        key: unknown_error[key]
        for key in ("code", "message", "status_code")
    } == {
        "code": "provider_call_failed",
        "message": "The model request failed.",
        "status_code": 502,
    }
    assert isinstance(unknown_error["conversation_id"], str)
    assert isinstance(unknown_error["assistant_message_id"], str)
    unknown_detail = client.get(
        f"/api/conversations/{unknown_error['conversation_id']}",
        headers=ordinary_user_headers,
    ).json()
    assert unknown_detail["messages"][-1]["id"] == unknown_error[
        "assistant_message_id"
    ]
    assert unknown_detail["messages"][-1]["status"] == "failed"
    assert private_detail not in unknown_failure.text


@pytest.mark.parametrize(
    ("runtime_code", "expected_code", "expected_message"),
    [
        (
            "attachment_unavailable",
            "attachment_unavailable",
            "Attachment content is unavailable.",
        ),
        (
            "attachment_corrupt",
            "attachment_corrupt",
            "Attachment content failed integrity validation.",
        ),
        (
            "private_internal_attachment_code",
            "attachment_unavailable",
            "Attachment content is unavailable.",
        ),
    ],
)
def test_stream_attachment_runtime_failure_is_stable_503_with_receipt_ids(
    client,
    ordinary_user_headers,
    runtime_code: str,
    expected_code: str,
    expected_message: str,
) -> None:
    from app.services.attachment_runtime_loader import AttachmentRuntimeError

    class FailingRuntime:
        def prepare_turn(self, turn):
            return turn

        def stream_turn(self, _turn, *, observer):
            del observer
            raise AttachmentRuntimeError(runtime_code)

    agent = _create_api_agent(client, ordinary_user_headers)
    client.app.state.generation_service.runtime = FailingRuntime()

    response = client.post(
        "/api/conversations/stream",
        headers=ordinary_user_headers,
        json={"text": "read attachment", "agent_id": agent["id"]},
    )

    events = _parse_sse(response)
    assert [event for event, _payload in events] == ["accepted", "error"]
    error = events[-1][1]
    assert {
        key: error[key]
        for key in ("code", "message", "status_code")
    } == {
        "code": expected_code,
        "message": expected_message,
        "status_code": 503,
    }
    assert isinstance(error["conversation_id"], str)
    assert isinstance(error["assistant_message_id"], str)
    assert runtime_code not in response.text or runtime_code == expected_code

    detail = client.get(
        f"/api/conversations/{error['conversation_id']}",
        headers=ordinary_user_headers,
    ).json()
    assistant = detail["messages"][-1]
    assert assistant["id"] == error["assistant_message_id"]
    assert assistant["status"] == "failed"
    assert assistant["metadata"]["error_code"] == expected_code
