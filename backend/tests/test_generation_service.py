from __future__ import annotations

import asyncio
from collections.abc import Callable, Collection, Iterator, Sequence
from dataclasses import FrozenInstanceError
import hashlib
import json
import logging
import math
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.core.request_context import request_id_context
from app.db import models
from app.db.session import session_scope
from app.repositories.attachment_repository import AttachmentRepository
from app.repositories.conversation_repository import ConversationRepository
from app.schemas.agents import AgentConfig, AgentPutRequest
from app.schemas.conversations import ConversationSendRequest
from app.schemas.modeling import ModelRef
from app.schemas.workspaces import WorkspaceConfig
from app.services.agent_home_service import AgentHomeService
from app.services.agent_runtime import AgentRuntime, RuntimeTerminalError, RuntimeTurn
from app.services.attachment_runtime_loader import (
    AttachmentRuntimeError,
    AttachmentRuntimeLoader,
)
from app.services.context_assembler import ContextAssembler
from app.services.agent_service import AgentService, ResolvedAgentConfig
from app.services.generation_service import (
    GenerationEvent,
    GenerationService,
    PreparedGeneration,
    PreparedGenerationError,
    ProviderMetricsInvalid,
)
from app.services.model_service import ModelService
from app.services.platform_prompt_service import PlatformPromptService
from app.services.runtime_types import ProviderCallMetrics, RuntimeObserver, ToolResult
from app.services.token_counter import RuntimeTokenCounter
from app.storage.object_store import ObjectStoreUnavailable
from tests.fake_object_store import FakeObjectStore


class RecordingRuntime:
    def __init__(
        self,
        *,
        chunks: tuple[str | ToolResult, ...] = ("answer",),
        error: Exception | None = None,
        on_start: Callable[[RuntimeTurn], None] | None = None,
        provider_calls: tuple[ProviderCallMetrics, ...] = (),
    ) -> None:
        self.chunks = chunks
        self.error = error
        self.on_start = on_start
        self.provider_calls = provider_calls
        self.calls: list[RuntimeTurn] = []
        self.prepare_calls: list[RuntimeTurn] = []

    def prepare_turn(self, turn: RuntimeTurn) -> RuntimeTurn:
        self.prepare_calls.append(turn)
        return turn

    def stream_turn(
        self,
        turn: RuntimeTurn,
        *,
        observer: RuntimeObserver,
    ) -> Iterator[str | ToolResult]:
        self.calls.append(turn)
        if self.on_start is not None:
            self.on_start(turn)
        return self._stream(observer)

    def _stream(self, observer: RuntimeObserver) -> Iterator[str | ToolResult]:
        for provider_call in self.provider_calls:
            observer(provider_call)
        yield from self.chunks
        if self.error is not None:
            raise self.error


def _provider_call(
    *,
    call_index: int = 1,
    provider: str = "openai",
    model: str = "gpt-test",
    provider_request_id: str | None = "provider-request-1",
    input_tokens: int | None = 12,
    output_tokens: int | None = 4,
    first_token_latency_ms: float | None = 20.0,
    duration_ms: float = 30.0,
    status: str = "completed",
    unavailable_fields: tuple[str, ...] | None = None,
) -> ProviderCallMetrics:
    values = (
        ("provider_request_id", provider_request_id),
        ("input_tokens", input_tokens),
        ("output_tokens", output_tokens),
        ("first_token_latency_ms", first_token_latency_ms),
    )
    return ProviderCallMetrics(
        call_index=call_index,
        provider=provider,
        model=model,
        provider_request_id=provider_request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        first_token_latency_ms=first_token_latency_ms,
        duration_ms=duration_ms,
        status=status,  # type: ignore[arg-type]
        unavailable_fields=(
            unavailable_fields
            if unavailable_fields is not None
            else tuple(field for field, value in values if value is None)
        ),
    )


@pytest.fixture
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'generation.db'}",
        connect_args={"check_same_thread": False},
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    yield factory
    engine.dispose()


def _settings(tmp_path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "data_dir": tmp_path,
        "database_url": f"sqlite:///{tmp_path / 'generation.db'}",
        "qwen_api_key": "test-qwen-key",
        "qwen_chat_models": "qwen3.7-plus,qwen3.7-max",
        "default_chat_provider": "qwen",
        "app_version": "generation-test",
        "chat_context_token_budget": 16_000,
    }
    values.update(overrides)
    budget = values["chat_context_token_budget"]
    assert isinstance(budget, int)
    reserve = min(4_096, budget - 1)
    values.setdefault("tool_result_token_reserve", reserve)
    values.setdefault("image_input_token_reserve", reserve)
    return Settings(_env_file=None, **values)


def _add_user(
    session: Session,
    *,
    user_id: int = 1,
    username: str = "alice",
) -> models.User:
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
    agent_id: str = "agent-1",
    owner_id: int = 1,
    prompt: str = "Prompt v1",
    default_model: ModelRef | None = ModelRef(provider="qwen", model="qwen3.7-plus"),
    home_workspace_id: str | None = None,
) -> models.Resource:
    agent = models.Resource(
        id=agent_id,
        user_id=owner_id,
        resource_type="agent",
        name=f"Agent {agent_id}",
        config_json=AgentConfig(
            system_prompt=prompt,
            default_model=default_model,
            home_workspace_id=home_workspace_id,
        ).model_dump(mode="json"),
    )
    session.add(agent)
    session.flush()
    return agent


def _add_workspace(
    session: Session,
    *,
    workspace_id: str = "workspace-1",
    owner_id: int = 1,
) -> models.Resource:
    workspace = models.Resource(
        id=workspace_id,
        user_id=owner_id,
        resource_type="workspace",
        name=f"Workspace {workspace_id}",
        config_json=WorkspaceConfig(
            workspace_type="agent_home",
            initial_agents_md="# Agent Home",
        ).model_dump(mode="json"),
    )
    session.add(workspace)
    session.flush()
    return workspace


def _seed_user_and_agent(
    factory: sessionmaker[Session],
    *,
    prompt: str = "Prompt v1",
    default_model: ModelRef | None = ModelRef(provider="qwen", model="qwen3.7-plus"),
) -> tuple[int, str]:
    with session_scope(factory) as session:
        user = _add_user(session)
        agent = _add_agent(
            session,
            owner_id=user.id,
            prompt=prompt,
            default_model=default_model,
        )
        return user.id, agent.id


def _add_conversation(
    session: Session,
    *,
    user_id: int,
    agent_id: str,
    status: str = "idle",
    model_override: ModelRef | None = None,
) -> models.Conversation:
    conversation = models.Conversation(
        user_id=user_id,
        agent_id=agent_id,
        title="Existing",
        status=status,
        model_override_json=(
            model_override.model_dump(mode="json") if model_override is not None else None
        ),
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
    metadata: dict[str, object] | None = None,
) -> models.Message:
    message = models.Message(
        user_id=user_id,
        conversation_id=conversation_id,
        sequence=sequence,
        role=role,
        status=status,
        content=content,
        provider="qwen" if role == "assistant" else None,
        model="qwen3.7-plus" if role == "assistant" else None,
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
    message_id: str | None = None,
    filename: str | None = None,
) -> models.Attachment:
    attachment = models.Attachment(
        id=attachment_id,
        user_id=user_id,
        message_id=message_id,
        original_filename=filename or f"{attachment_id}.txt",
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


def _add_runtime_text_attachment(
    session: Session,
    store: FakeObjectStore,
    *,
    attachment_id: str,
    user_id: int,
    parsed: bytes = b"parsed source",
    persist_object: bool = True,
) -> models.Attachment:
    source = b"original source"
    source_key = f"users/{user_id}/attachments/{attachment_id}/source.txt"
    parsed_key = f"users/{user_id}/attachments/{attachment_id}/parsed.txt"
    if persist_object:
        store.put(source_key, source)
        store.put(parsed_key, parsed)
    attachment = models.Attachment(
        id=attachment_id,
        user_id=user_id,
        original_filename="source.txt",
        object_key=source_key,
        parsed_object_key=parsed_key,
        mime_type="text/plain",
        size_bytes=len(source),
        content_hash=hashlib.sha256(source).hexdigest(),
        parsed_size_bytes=len(parsed),
        parsed_content_hash=hashlib.sha256(parsed).hexdigest(),
    )
    session.add(attachment)
    session.flush()
    return attachment


class RecordingModelRuntimeService:
    def __init__(self, chunks: tuple[str, ...] = ("answer",)) -> None:
        self.chunks = chunks
        self.calls: list[list[dict[str, object]]] = []

    def stream_turn(
        self,
        messages: list[dict[str, object]],
        *,
        provider: str,
        model: str,
        call_index: int,
        observer: RuntimeObserver,
        tools: object | None = None,
    ) -> Iterator[str]:
        del tools
        self.calls.append(messages)
        observer(
            ProviderCallMetrics(
                call_index=call_index,
                provider=provider,
                model=model,
                provider_request_id=None,
                input_tokens=None,
                output_tokens=None,
                first_token_latency_ms=None,
                duration_ms=0.0,
                status="completed",
                unavailable_fields=(
                    "provider_request_id",
                    "input_tokens",
                    "output_tokens",
                    "first_token_latency_ms",
                ),
            )
        )
        return iter(self.chunks)


def _agent_runtime(
    *,
    settings: Settings,
    store: FakeObjectStore,
    model_service: object,
) -> AgentRuntime:
    token_counter = RuntimeTokenCounter(
        image_input_token_reserve=settings.image_input_token_reserve
    )
    return AgentRuntime(
        model_service=model_service,  # type: ignore[arg-type]
        prompt_service=PlatformPromptService(),
        attachment_loader=AttachmentRuntimeLoader(object_store=store),
        context_assembler=ContextAssembler(
            token_budget=settings.chat_context_token_budget,
            token_counter=token_counter,
        ),
        agent_home=AgentHomeService(
            store=store,
            max_file_bytes=settings.agent_home_max_file_bytes,
        ),
        token_counter=token_counter,
        tool_result_token_reserve=settings.tool_result_token_reserve,
        capability_providers=(),
    )


def _attachment_runtime(
    *,
    settings: Settings,
    store: FakeObjectStore,
    model_service: RecordingModelRuntimeService | None = None,
) -> tuple[AgentRuntime, RecordingModelRuntimeService]:
    model = model_service or RecordingModelRuntimeService()
    return (
        _agent_runtime(settings=settings, store=store, model_service=model),
        model,
    )


def _error_code(error: HTTPException) -> str:
    assert isinstance(error.detail, dict)
    code = error.detail.get("code")
    assert isinstance(code, str)
    return code


def test_generation_contract_is_frozen_and_settings_expose_context_budget(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    settings = _settings(tmp_path, chat_context_token_budget=1234)
    event = GenerationEvent(event="delta", data={"text": "x"})
    prepared = PreparedGeneration(
        conversation_id="conversation-1",
        user_message_id="user-message-1",
        assistant_message_id="message-1",
        attachment_ids=("attachment-1",),
        runtime_turn=RuntimeTurn(
            context=None,  # type: ignore[arg-type]
            agent_prompt="Prompt",
            provider="qwen",
            model="qwen3.7-plus",
            turns=(),
        ),
    )

    assert settings.chat_context_token_budget == 1234
    with pytest.raises(FrozenInstanceError):
        event.event = "result"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        prepared.conversation_id = "changed"  # type: ignore[misc]


def test_send_request_rejects_invalid_attachment_ids() -> None:
    with pytest.raises(ValidationError, match="attachment ids must be unique"):
        ConversationSendRequest(
            text="blocked",
            agent_id="agent-1",
            attachment_ids=["a", "a"],
        )
    with pytest.raises(ValidationError):
        ConversationSendRequest(
            text="blocked",
            agent_id="agent-1",
            attachment_ids=[""],
        )
    with pytest.raises(ValidationError):
        ConversationSendRequest(
            text="blocked",
            agent_id="agent-1",
            attachment_ids=["x" * 37],
        )
    with pytest.raises(ValidationError):
        ConversationSendRequest(
            text="blocked",
            agent_id="agent-1",
            attachment_ids=[f"attachment-{index}" for index in range(11)],
        )


def test_stream_turn_binds_requested_drafts_in_order_before_runtime_starts(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    requested_ids = ["attachment-b", "attachment-a"]
    with session_scope(session_factory) as session:
        for attachment_id in reversed(requested_ids):
            _add_attachment(
                session,
                attachment_id=attachment_id,
                user_id=user_id,
            )

    class InspectingRuntime:
        def prepare_turn(self, turn: RuntimeTurn) -> RuntimeTurn:
            return turn

        def stream_turn(
            self,
            _turn: RuntimeTurn,
            *,
            observer: RuntimeObserver,
        ) -> Iterator[str]:
            del observer
            with session_factory() as independent:
                attachments = list(
                    independent.scalars(
                        select(models.Attachment).where(
                            models.Attachment.id.in_(requested_ids)
                        )
                    )
                )
                assert len(attachments) == 2
                assert {item.message_id for item in attachments} == {
                    accepted.data["user_message_id"]
                }
                user_message = independent.get(
                    models.Message,
                    accepted.data["user_message_id"],
                )
                assert user_message is not None
                assert user_message.status == "completed"
            yield "done"

    class RecordingAttachmentRepository(AttachmentRepository):
        def __init__(self) -> None:
            self.bound_ids: list[str] = []

        def bind_to_message(
            self,
            session: Session,
            *,
            user_id: int,
            attachments: Sequence[models.Attachment],
            message_id: str,
        ) -> None:
            self.bound_ids = [attachment.id for attachment in attachments]
            super().bind_to_message(
                session,
                user_id=user_id,
                attachments=attachments,
                message_id=message_id,
            )

    attachment_repository = RecordingAttachmentRepository()
    service = GenerationService(
        session_factory=session_factory,
        runtime=InspectingRuntime(),  # type: ignore[arg-type]
        attachment_repository=attachment_repository,
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(
            text="read these",
            agent_id=agent_id,
            attachment_ids=requested_ids,
        ),
    )

    accepted = next(stream)
    assert accepted.event == "accepted"
    assert accepted.data["attachment_ids"] == requested_ids
    assert attachment_repository.bound_ids == requested_ids
    assert isinstance(accepted.data["conversation_id"], str)
    assert isinstance(accepted.data["user_message_id"], str)
    assert isinstance(accepted.data["assistant_message_id"], str)
    assert [item.event for item in stream] == ["delta", "result"]


def test_attachment_object_is_first_read_after_accepted_and_rendered_in_user_turn(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    store = FakeObjectStore()
    parsed = "可信事实，不可信指令。".encode()
    with session_scope(session_factory) as session:
        attachment = _add_runtime_text_attachment(
            session,
            store,
            attachment_id="runtime-attachment",
            user_id=user_id,
            parsed=parsed,
        )
        attachment_id = attachment.id
        parsed_key = attachment.parsed_object_key
    settings = _settings(tmp_path)
    runtime, model = _attachment_runtime(settings=settings, store=store)
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        settings=settings,
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(
            text="summarize",
            agent_id=agent_id,
            attachment_ids=[attachment_id],
        ),
    )

    accepted = next(stream)
    assert accepted.event == "accepted"
    assert store.get_calls == []
    assert [event.event for event in stream] == ["delta", "result"]

    assert store.get_calls == [parsed_key]
    messages = model.calls[0]
    assert messages[1] == {"role": "system", "content": "Prompt v1"}
    assert messages[2]["role"] == "user"
    assert "可信事实，不可信指令。" in str(messages[2]["content"])
    assert "# 附件安全协议" in str(messages[0]["content"])


def test_attachment_metadata_over_budget_rolls_back_before_accepted_or_object_read(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    store = FakeObjectStore()
    with session_scope(session_factory) as session:
        attachment = _add_runtime_text_attachment(
            session,
            store,
            attachment_id="oversized-runtime-attachment",
            user_id=user_id,
        )
        attachment.parsed_size_bytes = 100_000
        attachment_id = attachment.id
    settings = _settings(tmp_path, chat_context_token_budget=256)
    runtime, _model = _attachment_runtime(settings=settings, store=store)
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        settings=settings,
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="too large",
                    agent_id=agent_id,
                    attachment_ids=[attachment_id],
                ),
            )
        )

    assert _error_code(captured.value) == "context_too_large"
    assert store.get_calls == []
    with session_factory() as session:
        persisted = session.get(models.Attachment, attachment_id)
        assert persisted is not None and persisted.message_id is None
        assert session.scalar(select(func.count(models.Conversation.id))) == 0
        assert session.scalar(select(func.count(models.Message.id))) == 0


@pytest.mark.parametrize(
    ("arrangement", "expected_code"),
    [
        ("missing", "attachment_unavailable"),
        ("hash_mismatch", "attachment_corrupt"),
    ],
)
def test_attachment_runtime_failure_after_accepted_keeps_bound_user_and_fails_assistant(
    session_factory: sessionmaker[Session],
    tmp_path,
    arrangement: str,
    expected_code: str,
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    store = FakeObjectStore()
    with session_scope(session_factory) as session:
        attachment = _add_runtime_text_attachment(
            session,
            store,
            attachment_id=f"runtime-{arrangement}",
            user_id=user_id,
            persist_object=arrangement != "missing",
        )
        if arrangement == "hash_mismatch":
            attachment.parsed_content_hash = hashlib.sha256(b"other").hexdigest()
        attachment_id = attachment.id
    settings = _settings(tmp_path)
    runtime, model = _attachment_runtime(settings=settings, store=store)
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        settings=settings,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(
            text="keep me",
            agent_id=agent_id,
            attachment_ids=[attachment_id],
        ),
    )

    accepted = next(stream)
    assert store.get_calls == []
    with pytest.raises(AttachmentRuntimeError) as captured:
        list(stream)
    assert captured.value.code == expected_code
    assert model.calls == []

    with session_factory() as session:
        attachment = session.get(models.Attachment, attachment_id)
        user_message = session.get(models.Message, accepted.data["user_message_id"])
        assistant = session.get(
            models.Message,
            accepted.data["assistant_message_id"],
        )
        conversation = session.get(
            models.Conversation,
            accepted.data["conversation_id"],
        )
        assert attachment is not None and user_message is not None
        assert attachment.message_id == user_message.id
        assert user_message.content == "keep me"
        assert assistant is not None and assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == expected_code
        assert conversation is not None and conversation.status == "idle"


def test_prepare_commit_failure_rolls_back_turn_binding_and_emits_nothing(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    attachment_id = "attachment-1"
    with session_scope(session_factory) as session:
        _add_attachment(
            session,
            attachment_id=attachment_id,
            user_id=user_id,
        )
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    def fail_commit(_session: Session) -> None:
        raise SQLAlchemyError("commit failed")

    event.listen(session_factory.class_, "before_commit", fail_commit, once=True)
    with pytest.raises(SQLAlchemyError, match="commit failed"):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="read this",
                    agent_id=agent_id,
                    attachment_ids=[attachment_id],
                ),
            )
        )

    with session_factory() as independent:
        attachment = independent.get(models.Attachment, attachment_id)
        assert attachment is not None and attachment.message_id is None
        assert independent.scalar(select(func.count(models.Message.id))) == 0
        assert independent.scalar(select(func.count(models.Conversation.id))) == 0


def test_stream_turn_rejects_any_unavailable_attachment_and_rolls_back_whole_turn(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        other = _add_user(session, user_id=2, username="bob")
        own_draft = _add_attachment(
            session,
            attachment_id="own-draft",
            user_id=user_id,
        )
        other_draft = _add_attachment(
            session,
            attachment_id="other-draft",
            user_id=other.id,
        )
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        bound_message = _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            status="completed",
            content="already sent",
        )
        bound = _add_attachment(
            session,
            attachment_id="bound",
            user_id=user_id,
            message_id=bound_message.id,
        )
        initial_message_count = session.scalar(select(func.count(models.Message.id)))
        initial_conversation_count = session.scalar(
            select(func.count(models.Conversation.id))
        )
        ids = (own_draft.id, other_draft.id, bound.id)

    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )
    for unavailable_id in ("missing", ids[1], ids[2]):
        with pytest.raises(HTTPException) as captured:
            list(
                service.stream_turn(
                    user_id=user_id,
                    request=ConversationSendRequest(
                        text="blocked",
                        agent_id=agent_id,
                        attachment_ids=[ids[0], unavailable_id],
                    ),
                )
            )
        assert captured.value.status_code == 409
        assert _error_code(captured.value) == "attachment_not_ready"

    with session_factory() as session:
        own = session.get(models.Attachment, ids[0])
        assert own is not None and own.message_id is None
        assert session.scalar(select(func.count(models.Message.id))) == initial_message_count
        assert (
            session.scalar(select(func.count(models.Conversation.id)))
            == initial_conversation_count
        )


def test_turn_is_committed_before_runtime_starts(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)

    class InspectingGeneratorRuntime:
        def prepare_turn(self, turn: RuntimeTurn) -> RuntimeTurn:
            return turn

        def stream_turn(
            self,
            turn: RuntimeTurn,
            *,
            observer: RuntimeObserver,
        ) -> Iterator[str]:
            del observer
            assert isinstance(turn.context.agent_config, ResolvedAgentConfig)
            with session_factory() as session:
                conversation = session.scalar(select(models.Conversation))
                messages = list(
                    session.scalars(select(models.Message).order_by(models.Message.sequence))
                )
                assert conversation is not None
                assert conversation.status == "generating"
                assert [(item.role, item.status) for item in messages] == [
                    ("user", "completed"),
                    ("assistant", "pending"),
                ]
                assert [item.user.text for item in turn.turns] == ["hello"]
            yield "answer"

    service = GenerationService(
        session_factory=session_factory,
        runtime=InspectingGeneratorRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    events = list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(
                text="  hello  ",
                agent_id=agent_id,
            ),
        )
    )

    assert [event.event for event in events] == ["accepted", "delta", "result"]
    assert events[-1].data["message"]["status"] == "completed"  # type: ignore[index]
    with session_factory() as session:
        conversation = session.scalar(select(models.Conversation))
        messages = list(session.scalars(select(models.Message).order_by(models.Message.sequence)))
        assert conversation is not None
        assert conversation.status == "idle"
        assert conversation.title == "hello"
        assert messages[0].content == "hello"
        assert messages[1].content == "answer"


def test_generation_metadata_records_bound_request_id_for_send_and_retry(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=(),
            error=RuntimeError("provider failed"),
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    with request_id_context("request-initial-1"):
        with pytest.raises(RuntimeError, match="provider failed"):
            list(
                service.stream_turn(
                    user_id=user_id,
                    request=ConversationSendRequest(
                        text="question",
                        agent_id=agent_id,
                    ),
                )
            )

    with session_factory() as session:
        initial = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        assert initial is not None
        failed_message_id = initial.id
        conversation_id = initial.conversation_id
        assert initial.metadata_json["request_id"] == "request-initial-1"

    service.runtime = RecordingRuntime()  # type: ignore[assignment]
    with request_id_context("request-retry-2"):
        list(
            service.retry_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                failed_message_id=failed_message_id,
            )
        )

    with session_factory() as session:
        assistants = list(
            session.scalars(
                select(models.Message)
                .where(models.Message.role == "assistant")
                .order_by(models.Message.sequence)
            )
        )
        assert assistants[0].metadata_json["request_id"] == "request-initial-1"
        assert assistants[1].metadata_json["request_id"] == "request-retry-2"
        assert assistants[1].metadata_json["retry_of_message_id"] == failed_message_id


def test_generation_metadata_aggregates_multiple_provider_calls(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            provider_calls=(
                _provider_call(),
                _provider_call(
                    call_index=2,
                    provider_request_id=None,
                    input_tokens=None,
                    output_tokens=None,
                    first_token_latency_ms=None,
                    duration_ms=15.0,
                ),
            )
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=iter((0.0, 0.0, 0.1)).__next__,
    )

    events = list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(text="question", agent_id=agent_id),
        )
    )

    with session_factory() as session:
        assistant = session.get(
            models.Message,
            events[0].data["assistant_message_id"],
        )
        assert assistant is not None
        metadata = assistant.metadata_json
        assert metadata["conversation_id"] == assistant.conversation_id
        assert metadata["message_id"] == assistant.id
        assert metadata["provider_request_id"] == "provider-request-1"
        assert metadata["provider_calls"] == [
            {
                "call_index": 1,
                "provider": "openai",
                "model": "gpt-test",
                "provider_request_id": "provider-request-1",
                "input_tokens": 12,
                "output_tokens": 4,
                "first_token_latency_ms": 20.0,
                "duration_ms": 30.0,
                "status": "completed",
                "unavailable_fields": [],
            },
            {
                "call_index": 2,
                "provider": "openai",
                "model": "gpt-test",
                "provider_request_id": None,
                "input_tokens": None,
                "output_tokens": None,
                "first_token_latency_ms": None,
                "duration_ms": 15.0,
                "status": "completed",
                "unavailable_fields": [
                    "provider_request_id",
                    "input_tokens",
                    "output_tokens",
                    "first_token_latency_ms",
                ],
            },
        ]
        assert metadata["token_usage"] == {
            "input_tokens": 12,
            "output_tokens": 4,
            "incomplete": True,
        }
        assert metadata["first_token_latency_ms"] == 20.0
        assert metadata["total_duration_ms"] == 100.0
        serialized = json.dumps(metadata)
        assert "file body" not in serialized
        assert "/private/path" not in serialized


def test_streaming_checkpoint_and_completion_each_become_visible(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)

    def inspect_pending_before_model_call(_turn: RuntimeTurn) -> None:
        with session_factory() as session:
            assistant = session.scalar(
                select(models.Message).where(models.Message.role == "assistant")
            )
            assert assistant is not None
            assert assistant.status == "pending"

    clock = iter([0.0, 1.1, 1.2]).__next__
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=("partial", " answer"),
            on_start=inspect_pending_before_model_call,
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=clock,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    assert next(stream).event == "accepted"
    assert next(stream) == GenerationEvent(event="delta", data={"text": "partial"})
    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("streaming", "partial")
        assert conversation.status == "generating"

    assert [event.event for event in stream] == ["delta", "result"]
    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == (
            "completed",
            "partial answer",
        )
        assert conversation.status == "idle"


def test_provider_failure_keeps_user_partial_assistant_and_stable_error(
    session_factory: sessionmaker[Session], tmp_path, caplog
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    private_text = "accepted private text"
    provider_detail = "provider leaked private payload"
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=("partial",),
            error=RuntimeError(provider_detail),
            provider_calls=(
                _provider_call(
                    provider_request_id="provider-failed-1",
                    input_tokens=9,
                    output_tokens=None,
                    first_token_latency_ms=5.0,
                    duration_ms=11.0,
                    status="failed",
                ),
            ),
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=iter([0.0, 1.1, 1.2]).__next__,
    )

    with pytest.raises(RuntimeError, match="provider leaked"):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text=private_text,
                    agent_id=agent_id,
                ),
            )
        )

    with session_factory() as session:
        messages = list(session.scalars(select(models.Message).order_by(models.Message.sequence)))
        conversation = session.scalar(select(models.Conversation))
        assert messages[0].content == private_text
        assert (messages[1].status, messages[1].content) == ("failed", "partial")
        assert messages[1].metadata_json["error_code"] == "provider_call_failed"
        assert messages[1].metadata_json["provider_calls"][0]["status"] == "failed"
        assert (
            messages[1].metadata_json["provider_request_id"]
            == "provider-failed-1"
        )
        assert messages[1].metadata_json["token_usage"] == {
            "input_tokens": 9,
            "output_tokens": 0,
            "incomplete": True,
        }
        assert messages[1].metadata_json["total_duration_ms"] == 1_200.0
        assert provider_detail not in str(messages[1].metadata_json)
        assert conversation is not None and conversation.status == "idle"
    assert private_text not in caplog.text
    assert provider_detail not in caplog.text


def test_http_failure_code_is_persisted_without_prompt_or_detail(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    failure = HTTPException(
        status_code=503,
        detail={"code": "knowledge_unavailable", "message": "private detail"},
    )
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(error=failure, chunks=()),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    with pytest.raises(HTTPException):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="question", agent_id=agent_id),
            )
        )

    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        assert assistant is not None
        assert assistant.metadata_json["error_code"] == "knowledge_unavailable"
        assert "private detail" not in str(assistant.metadata_json)


def test_tool_result_is_internal_and_persists_only_safe_success_audit(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    secret_content = "private file body must never leave the runtime"
    secret_path = "private/learning-notes.md"
    digest = "a" * 64
    etag = "opaque-etag-1"
    tool_result = ToolResult(
        call_id="call-1",
        content=secret_content,
        metadata={
            "tool": "read_file",
            "path_sha256": digest,
            "etag": etag,
            "path": secret_path,
            "body": secret_content,
            "code": "must-not-appear-on-success",
            "terminal": True,
        },
    )
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=(tool_result, "answer"),
            provider_calls=(_provider_call(),),
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    events = list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(text="question", agent_id=agent_id),
        )
    )

    assert [event.event for event in events] == ["accepted", "delta", "result"]
    assert events[1].data == {"text": "answer"}
    assert secret_content not in str(events)
    assert secret_path not in str(events)
    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        assert assistant is not None
        assert assistant.status == "completed"
        assert assistant.content == "answer"
        assert assistant.metadata_json["tool_calls"] == [
            {
                "call_id": "call-1",
                "tool": "read_file",
                "status": "completed",
                "path_sha256": digest,
                "etag": etag,
            }
        ]
        assert assistant.metadata_json["provider_calls"][0]["call_index"] == 1
        assert assistant.metadata_json["provider_calls"][0]["status"] == "completed"
        assert secret_content not in str(assistant.metadata_json)
        assert secret_path not in str(assistant.metadata_json)


def test_knowledge_success_audit_is_bounded_and_survives_later_failure(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    private_source = "private Knowledge source body"
    sources = [
        {
            "document_id": f"document-{index}",
            "collection_id": "collection-1",
            "filename": f"policy-{index}.md",
            "index_generation": 4,
            "content_hash": f"sha256-{index}",
            "chunk_index": index,
            "score": 0.9,
            "content": private_source,
            "object_key": "users/7/private-object-key",
        }
        for index in range(21)
    ]
    knowledge_result = ToolResult(
        call_id="call-knowledge",
        content=private_source,
        metadata={
            "tool": "search_knowledge",
            "mode": "rag",
            "sources": sources,
            "prompt": "malicious extra audit field",
            "content": private_source,
        },
    )
    later_failure = RuntimeError("model failed after Knowledge lookup")
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=(knowledge_result,),
            error=later_failure,
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    with pytest.raises(RuntimeError) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="question",
                    agent_id=agent_id,
                ),
            )
        )

    assert captured.value is later_failure
    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        assert assistant is not None
        assert assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == "provider_call_failed"
        audit = assistant.metadata_json["tool_calls"][0]
        assert audit["call_id"] == "call-knowledge"
        assert audit["tool"] == "search_knowledge"
        assert audit["status"] == "completed"
        assert audit["mode"] == "rag"
        assert len(audit["sources"]) == 20
        assert audit["sources"][0] == {
            "document_id": "document-0",
            "collection_id": "collection-1",
            "filename": "policy-0.md",
            "index_generation": 4,
            "content_hash": "sha256-0",
            "chunk_index": 0,
            "score": 0.9,
        }
        assert audit["sources"][-1]["document_id"] == "document-19"
        assert private_source not in str(assistant.metadata_json)
        assert "private-object-key" not in str(assistant.metadata_json)
        assert "malicious extra audit field" not in str(assistant.metadata_json)


def test_terminal_runtime_error_preserves_error_audit_and_stable_failure_code(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    private_content = "oversized private tool result"
    private_path = "private/oversized.md"
    terminal = RuntimeTerminalError(
        code="context_too_large",
        message="The conversation context is too large.",
        status_code=413,
    )
    audit = ToolResult(
        call_id="call-2",
        content=private_content,
        is_error=True,
        metadata={
            "tool": "read_file",
            "code": "context_too_large",
            "terminal": True,
            "path_sha256": "b" * 64,
            "etag": "must-not-appear-on-error",
            "path": private_path,
        },
    )
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=(audit,), error=terminal),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    assert next(stream).event == "accepted"
    with pytest.raises(RuntimeTerminalError) as captured:
        list(stream)

    assert captured.value is terminal
    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        assert assistant is not None
        assert assistant.status == "failed"
        assert assistant.content == ""
        assert assistant.metadata_json["error_code"] == "context_too_large"
        assert assistant.metadata_json["tool_calls"] == [
            {
                "call_id": "call-2",
                "tool": "read_file",
                "status": "error",
                "code": "context_too_large",
                "terminal": True,
            }
        ]
        assert private_content not in str(assistant.metadata_json)
        assert private_path not in str(assistant.metadata_json)


def test_agent_home_root_failure_happens_after_accepted_and_marks_turn_failed(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    with session_scope(session_factory) as session:
        user = _add_user(session)
        workspace = _add_workspace(session, owner_id=user.id)
        agent = _add_agent(
            session,
            owner_id=user.id,
            home_workspace_id=workspace.id,
        )
        user_id = user.id
        agent_id = agent.id

    private_detail = "private object-store failure"
    store = FakeObjectStore(
        get_error=ObjectStoreUnavailable(private_detail),
    )
    settings = _settings(tmp_path)
    runtime, model = _attachment_runtime(settings=settings, store=store)
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        settings=settings,
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    accepted = next(stream)
    assert accepted.event == "accepted"
    assert store.put_calls == []
    assert store.get_calls == []
    with pytest.raises(RuntimeTerminalError) as captured:
        list(stream)

    assert captured.value.code == "workspace_unavailable"
    assert captured.value.status_code == 503
    assert model.calls == []
    with session_factory() as session:
        messages = list(
            session.scalars(select(models.Message).order_by(models.Message.sequence))
        )
        conversation = session.scalar(select(models.Conversation))
        assert conversation is not None and conversation.status == "idle"
        assert [(item.role, item.status, item.content) for item in messages] == [
            ("user", "completed", "question"),
            ("assistant", "failed", ""),
        ]
        assert messages[1].metadata_json["error_code"] == "workspace_unavailable"
        assert private_detail not in str(messages[1].metadata_json)


def test_whitespace_only_text_is_rejected_before_any_write(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text=" \n\t ", agent_id=agent_id),
            )
        )

    assert captured.value.status_code == 400
    assert _error_code(captured.value) == "chat_message_empty"
    with session_factory() as session:
        assert session.scalar(select(models.Conversation)) is None
        assert session.scalar(select(models.Message)) is None
    assert runtime.calls == []


def test_new_turn_requires_agent_and_existing_turn_locks_agent(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        second = _add_agent(session, agent_id="agent-2", owner_id=user_id)
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
        second_id = second.id
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    with pytest.raises(HTTPException) as missing:
        list(service.stream_turn(user_id=user_id, request=ConversationSendRequest(text="x")))
    assert _error_code(missing.value) == "agent_required"

    with pytest.raises(HTTPException) as changed:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="x",
                    conversation_id=conversation_id,
                    agent_id=second_id,
                ),
            )
        )
    assert _error_code(changed.value) == "agent_locked"


def test_generating_conversation_rejects_second_turn(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
            status="generating",
        )
        conversation_id = conversation.id
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="second",
                    conversation_id=conversation_id,
                ),
            )
        )

    assert captured.value.status_code == 409
    assert _error_code(captured.value) == "generation_in_progress"
    assert runtime.calls == []


def test_existing_conversation_uses_live_agent_prompt_and_default_model(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    settings = _settings(tmp_path)
    with session_scope(session_factory) as session:
        user = _add_user(session)
        admin = _add_user(session, user_id=2, username="admin")
        admin.role = "admin"
        agent = _add_agent(session, owner_id=0)
        user_id = user.id
        admin_id = admin.id
        agent_id = agent.id
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=settings,
        clock=lambda: 0.0,
    )
    first = list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(text="first", agent_id=agent_id),
        )
    )
    conversation_id = first[-1].data["conversation_id"]

    with session_scope(session_factory) as session:
        admin = session.get(models.User, admin_id)
        assert admin is not None
        AgentService(settings=settings).put_agent(
            session,
            actor=admin,
            agent_id=agent_id,
            payload=AgentPutRequest(
                name=f"Agent {agent_id}",
                config=AgentConfig(
                    system_prompt="Prompt v2",
                    default_model=ModelRef(provider="qwen", model="qwen3.7-max"),
                ),
                is_active=True,
            ),
        )

    list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(
                text="second",
                conversation_id=conversation_id,  # type: ignore[arg-type]
            ),
        )
    )

    assert [(call.agent_prompt, call.model) for call in runtime.calls] == [
        ("Prompt v1", "qwen3.7-plus"),
        ("Prompt v2", "qwen3.7-max"),
    ]
    with session_factory() as session:
        conversation = session.get(models.Conversation, conversation_id)
        assert conversation is not None
        assert conversation.agent_id == agent_id
        assert conversation.model_override_json is None


def test_existing_turn_rejects_request_model_override_instead_of_ignoring_it(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="second",
                    conversation_id=conversation_id,
                    model_override=ModelRef(
                        provider="qwen",
                        model="qwen3.7-max",
                    ),
                ),
            )
        )

    assert captured.value.status_code == 400
    assert _error_code(captured.value) == "model_override_not_allowed"
    assert runtime.calls == []
    with session_factory() as session:
        conversation = session.get(models.Conversation, conversation_id)
        assert conversation is not None and conversation.status == "idle"
        assert session.scalar(select(models.Message)) is None


def test_model_precedence_is_override_then_agent_default_then_system_default(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    with session_scope(session_factory) as session:
        user = _add_user(session)
        override_agent = _add_agent(
            session,
            agent_id="override-agent",
            owner_id=user.id,
            default_model=ModelRef(provider="qwen", model="qwen3.7-plus"),
        )
        default_agent = _add_agent(
            session,
            agent_id="default-agent",
            owner_id=user.id,
            default_model=ModelRef(provider="qwen", model="qwen3.7-max"),
        )
        system_agent = _add_agent(
            session,
            agent_id="system-agent",
            owner_id=user.id,
            default_model=None,
        )
        user_id = user.id
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    requests = (
        ConversationSendRequest(
            text="override",
            agent_id=override_agent.id,
            model_override=ModelRef(provider="qwen", model="qwen3.7-max"),
        ),
        ConversationSendRequest(text="agent", agent_id=default_agent.id),
        ConversationSendRequest(text="system", agent_id=system_agent.id),
    )
    for request in requests:
        list(service.stream_turn(user_id=user_id, request=request))

    assert [call.model for call in runtime.calls] == [
        "qwen3.7-max",
        "qwen3.7-max",
        "qwen3.7-plus",
    ]


@pytest.mark.parametrize("unavailable", ["deleted", "inactive"])
def test_unavailable_agent_rejects_later_existing_turn(
    session_factory: sessionmaker[Session], tmp_path, unavailable: str
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
        agent = session.get(models.Resource, agent_id)
        assert agent is not None
        if unavailable == "deleted":
            agent.deleted_at = models._now()
        agent.is_active = False
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="later",
                    conversation_id=conversation_id,
                ),
            )
        )

    assert _error_code(captured.value) == "agent_unavailable"
    with session_factory() as session:
        assert session.scalar(select(models.Message)) is None


def test_failed_assistant_is_excluded_and_history_is_bounded_deterministically(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
        _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=1,
            role="user",
            status="completed",
            content="old-old",
        )
        _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=2,
            role="assistant",
            status="completed",
            content="reply",
        )
        _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=3,
            role="assistant",
            status="failed",
            content="failed partial",
        )
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path, chat_context_token_budget=3),
        clock=lambda: 0.0,
    )

    list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(
                text="new",
                conversation_id=conversation_id,
            ),
        )
    )

    prepared_turn = runtime.prepare_calls[0]
    assert [item.user.text for item in prepared_turn.turns] == ["old-old", "new"]
    assert [
        assistant.text
        for item in prepared_turn.turns
        for assistant in item.assistants
    ] == ["reply"]
    assert runtime.calls[0].context.token_budget == 3
    assert runtime.calls[0].context.user_id == user_id


def test_oversized_current_message_rolls_back_prepare(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
    settings = _settings(tmp_path, chat_context_token_budget=200)

    class UnexpectedModel:
        def stream_turn(self, *_args, **_kwargs):
            raise AssertionError("model must not be called")

    store = FakeObjectStore()
    service = GenerationService(
        session_factory=session_factory,
        runtime=_agent_runtime(
            settings=settings,
            store=store,
            model_service=UnexpectedModel(),
        ),
        settings=settings,
    )

    with pytest.raises(HTTPException) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(
                    text="x" * 5_000,
                    conversation_id=conversation_id,
                ),
            )
        )

    assert _error_code(captured.value) == "context_too_large"
    with session_factory() as session:
        conversation = session.get(models.Conversation, conversation_id)
        assert conversation is not None and conversation.status == "idle"
        assert session.scalar(select(models.Message)) is None


def test_retry_adds_only_one_pending_assistant_and_uses_tenant_sequence(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        _add_user(session, user_id=2, username="bob")
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
        _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=1,
            role="user",
            status="completed",
            content="question",
        )
        failed = _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=2,
            role="assistant",
            status="failed",
            content="partial",
        )
        _add_message(
            session,
            user_id=2,
            conversation_id=conversation_id,
            sequence=99,
            role="assistant",
            status="completed",
            content="corrupt cross-tenant row",
        )
        failed_id = failed.id

    def inspect_retry_committed(turn: RuntimeTurn) -> None:
        with session_factory() as session:
            own_messages = list(
                session.scalars(
                    select(models.Message)
                    .where(models.Message.user_id == user_id)
                    .order_by(models.Message.sequence)
                )
            )
            assert [(item.role, item.status, item.sequence) for item in own_messages] == [
                ("user", "completed", 1),
                ("assistant", "failed", 2),
                ("assistant", "pending", 3),
            ]
            assert own_messages[-1].metadata_json["retry_of_message_id"] == failed_id
            assert [item.user.text for item in turn.turns] == ["question"]

    runtime = RecordingRuntime(on_start=inspect_retry_committed)
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    events = list(
        service.retry_turn(
            user_id=user_id,
            conversation_id=conversation_id,
            failed_message_id=failed_id,
        )
    )

    assert events[0].event == "accepted"
    assert events[0].data == {
        "conversation_id": conversation_id,
        "user_message_id": None,
        "assistant_message_id": events[0].data["assistant_message_id"],
        "attachment_ids": [],
    }
    assert events[-1].event == "result"
    with session_factory() as session:
        own_messages = list(
            session.scalars(
                select(models.Message)
                .where(models.Message.user_id == user_id)
                .order_by(models.Message.sequence)
            )
        )
        assert [item.role for item in own_messages] == [
            "user",
            "assistant",
            "assistant",
        ]
        assert own_messages[-1].status == "completed"


def test_retry_loads_retained_historical_attachment_without_rebinding_it(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    store = FakeObjectStore()
    parsed = "历史附件原文".encode()
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        user_message = _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            sequence=1,
            role="user",
            status="completed",
            content="历史问题",
        )
        attachment = _add_runtime_text_attachment(
            session,
            store,
            attachment_id="historical-runtime-attachment",
            user_id=user_id,
            parsed=parsed,
        )
        attachment.message_id = user_message.id
        _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            sequence=2,
            role="assistant",
            status="completed",
            content="历史回答",
        )
        failed = _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            sequence=3,
            role="assistant",
            status="failed",
            content="失败回答",
        )
        conversation_id = conversation.id
        user_message_id = user_message.id
        failed_id = failed.id
        attachment_id = attachment.id
        parsed_key = attachment.parsed_object_key

    class CountingAttachmentRepository(AttachmentRepository):
        def __init__(self) -> None:
            self.history_batches: list[tuple[str, ...]] = []

        def list_for_messages(
            self,
            session: Session,
            *,
            user_id: int,
            message_ids: Collection[str],
        ) -> list[models.Attachment]:
            self.history_batches.append(tuple(message_ids))
            return super().list_for_messages(
                session,
                user_id=user_id,
                message_ids=message_ids,
            )

    attachment_repository = CountingAttachmentRepository()
    settings = _settings(tmp_path)
    runtime, model = _attachment_runtime(settings=settings, store=store)
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        attachment_repository=attachment_repository,
        settings=settings,
        clock=lambda: 0.0,
    )
    stream = service.retry_turn(
        user_id=user_id,
        conversation_id=conversation_id,
        failed_message_id=failed_id,
    )

    accepted = next(stream)
    assert accepted.data["attachment_ids"] == []
    assert accepted.data["user_message_id"] is None
    assert store.get_calls == []
    assert [event.event for event in stream] == ["delta", "result"]

    assert store.get_calls == [parsed_key]
    assert len(attachment_repository.history_batches) == 1
    messages = model.calls[0]
    assert messages[1] == {"role": "system", "content": "Prompt v1"}
    assert messages[2]["role"] == "user"
    assert "历史附件原文" in str(messages[2]["content"])
    assert messages[3] == {"role": "assistant", "content": "历史回答"}
    with session_factory() as session:
        persisted = session.get(models.Attachment, attachment_id)
        assert persisted is not None
        assert persisted.message_id == user_message_id


def test_retry_rejects_wrong_tenant_message_and_generating_conversation(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        _add_user(session, user_id=2, username="bob")
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        conversation_id = conversation.id
        failed = _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation_id,
            sequence=1,
            role="assistant",
            status="failed",
            content="partial",
        )
        failed_id = failed.id
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    with pytest.raises(HTTPException) as wrong_tenant:
        list(
            service.retry_turn(
                user_id=2,
                conversation_id=conversation_id,
                failed_message_id=failed_id,
            )
        )
    assert _error_code(wrong_tenant.value) == "conversation_not_found"

    with session_scope(session_factory) as session:
        conversation = session.get(models.Conversation, conversation_id)
        assert conversation is not None
        conversation.status = "generating"
    with pytest.raises(HTTPException) as active:
        list(
            service.retry_turn(
                user_id=user_id,
                conversation_id=conversation_id,
                failed_message_id=failed_id,
            )
        )
    assert _error_code(active.value) == "generation_in_progress"


def test_generator_close_after_delta_fails_partial_and_releases_latch(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=("partial", "ignored")),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    assert next(stream).event == "accepted"
    assert next(stream).data == {"text": "partial"}
    stream.close()

    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("failed", "partial")
        assert assistant.metadata_json["error_code"] == "generation_cancelled"
        assert conversation.status == "idle"


def test_generator_close_closes_runtime_before_persisting_provider_audit(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)

    class AuditedOnCloseRuntime:
        def __init__(self) -> None:
            self.closed = False

        def prepare_turn(self, turn: RuntimeTurn) -> RuntimeTurn:
            return turn

        def stream_turn(
            self,
            _turn: RuntimeTurn,
            *,
            observer: RuntimeObserver,
        ) -> Iterator[str]:
            try:
                yield "partial"
                yield "ignored"
            finally:
                self.closed = True
                observer(
                    _provider_call(
                        provider_request_id="provider-cancelled-1",
                        output_tokens=None,
                        duration_ms=8.0,
                        status="failed",
                    )
                )

    runtime = AuditedOnCloseRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    accepted = next(stream)
    assert next(stream).data == {"text": "partial"}
    stream.close()

    assert runtime.closed is True
    with session_factory() as session:
        assistant = session.get(
            models.Message,
            accepted.data["assistant_message_id"],
        )
        assert assistant is not None
        assert assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == "generation_cancelled"
        assert assistant.metadata_json["provider_calls"][0]["status"] == "failed"
        assert (
            assistant.metadata_json["provider_request_id"]
            == "provider-cancelled-1"
        )


def test_generator_close_records_invalid_metrics_emitted_during_runtime_close(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)

    class InvalidAuditOnCloseRuntime:
        def prepare_turn(self, turn: RuntimeTurn) -> RuntimeTurn:
            return turn

        def stream_turn(
            self,
            _turn: RuntimeTurn,
            *,
            observer: RuntimeObserver,
        ) -> Iterator[str]:
            try:
                yield "partial"
                yield "ignored"
            finally:
                observer(_provider_call(input_tokens=10**5000))

    service = GenerationService(
        session_factory=session_factory,
        runtime=InvalidAuditOnCloseRuntime(),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    accepted = next(stream)
    assert next(stream).data == {"text": "partial"}
    stream.close()

    with session_factory() as session:
        assistant = session.get(
            models.Message,
            accepted.data["assistant_message_id"],
        )
        assert assistant is not None
        assert assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == "provider_metrics_invalid"
        assert assistant.metadata_json["provider_calls"] == []


@pytest.mark.parametrize(
    "provider_call",
    [
        _provider_call(input_tokens=10**5000),
        _provider_call(duration_ms=float("nan")),
        _provider_call(provider="private\nprompt"),
        _provider_call(call_index=2),
        _provider_call(status="unknown"),
        _provider_call(unavailable_fields=("provider_request_id",)),
    ],
)
def test_invalid_provider_metrics_fail_closed_without_persisting_unsafe_values(
    session_factory: sessionmaker[Session],
    tmp_path,
    provider_call: ProviderCallMetrics,
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(provider_calls=(provider_call,)),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    with pytest.raises(ProviderMetricsInvalid):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="question", agent_id=agent_id),
            )
        )

    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        assert assistant is not None
        assert assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == "provider_metrics_invalid"
        assert assistant.metadata_json["provider_calls"] == []
        assert assistant.metadata_json["token_usage"] == {
            "input_tokens": 0,
            "output_tokens": 0,
            "incomplete": True,
        }
        serialized = json.dumps(assistant.metadata_json)
        assert "private\\nprompt" not in serialized
        assert "NaN" not in serialized
        assert "100000000000000000000" not in serialized


def test_generation_clock_extremes_do_not_break_terminal_audit(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    clock = iter((10**5000, -(10**5000), float("inf"))).__next__
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            provider_calls=(_provider_call(duration_ms=0.0),)
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=clock,
    )

    events = list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(text="question", agent_id=agent_id),
        )
    )

    assert events[-1].event == "result"
    with session_factory() as session:
        assistant = session.get(
            models.Message,
            events[0].data["assistant_message_id"],
        )
        assert assistant is not None
        assert assistant.status == "completed"
        assert assistant.metadata_json["total_duration_ms"] == 0.0
        assert math.isfinite(assistant.metadata_json["total_duration_ms"])


def test_tool_only_empty_runtime_failure_preserves_tool_and_provider_audit(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    tool_result = ToolResult(
        call_id="call-empty",
        content="private file body",
        metadata={
            "tool": "read_file",
            "path_sha256": "c" * 64,
            "etag": "opaque-etag",
        },
    )
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=(tool_result,),
            provider_calls=(_provider_call(first_token_latency_ms=None),),
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    with pytest.raises(RuntimeError, match="empty model response"):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="question", agent_id=agent_id),
            )
        )

    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        assert assistant is not None
        assert assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == "provider_call_failed"
        assert assistant.metadata_json["provider_calls"][0][
            "first_token_latency_ms"
        ] is None
        assert assistant.metadata_json["tool_calls"] == [
            {
                "call_id": "call-empty",
                "tool": "read_file",
                "status": "completed",
                "path_sha256": "c" * 64,
                "etag": "opaque-etag",
            }
        ]
        assert "private file body" not in json.dumps(assistant.metadata_json)


def test_generator_close_immediately_after_accepted_fails_pending_and_releases_latch(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    runtime = RecordingRuntime(chunks=("must not start",))
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    accepted = next(stream)
    assert accepted.event == "accepted"
    stream.close()

    assert runtime.calls == []
    with session_factory() as session:
        assistant = session.get(
            models.Message,
            accepted.data["assistant_message_id"],
        )
        conversation = session.get(
            models.Conversation,
            accepted.data["conversation_id"],
        )
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("failed", "")
        assert assistant.metadata_json["error_code"] == "generation_cancelled"
        assert assistant.metadata_json["provider_calls"] == []
        assert assistant.metadata_json["token_usage"]["incomplete"] is True
        assert assistant.metadata_json["total_duration_ms"] == 0.0
        assert conversation.status == "idle"


def test_sse_wrapper_close_closes_generation_iterator_and_releases_latch(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    from app.api.sse import stream_generation_events

    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=("partial", "ignored")),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    events = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )
    body = stream_generation_events(events)

    assert next(body).startswith("event: accepted\n")
    body.close()

    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("failed", "")
        assert assistant.metadata_json["error_code"] == "generation_cancelled"
        assert conversation.status == "idle"


def test_sse_unknown_prepare_or_commit_failure_is_request_failed_without_ids() -> None:
    from app.api.sse import sse_event, stream_generation_events

    def failing_events() -> Iterator[GenerationEvent]:
        raise RuntimeError("private prepare detail")
        yield GenerationEvent(event="delta", data={"text": "unreachable"})

    body = list(stream_generation_events(failing_events()))

    assert body == [
        sse_event(
            "error",
            {
                "code": "request_failed",
                "message": "Request failed.",
                "status_code": 500,
            },
        )
    ]
    assert "private prepare detail" not in body[0]
    assert "conversation_id" not in body[0]
    assert "assistant_message_id" not in body[0]


def test_sse_unknown_committed_generation_failure_keeps_provider_error_and_ids() -> None:
    from app.api.sse import sse_event, stream_generation_events

    prepared = PreparedGeneration(
        conversation_id="conversation-1",
        user_message_id="user-message-1",
        assistant_message_id="assistant-message-1",
        runtime_turn=RuntimeTurn(
            context=None,  # type: ignore[arg-type]
            agent_prompt="Prompt",
            provider="qwen",
            model="qwen3.7-plus",
            turns=(),
        ),
    )

    def failing_events() -> Iterator[GenerationEvent]:
        raise PreparedGenerationError(
            prepared,
            RuntimeError("private provider detail"),
        )
        yield GenerationEvent(event="delta", data={"text": "unreachable"})

    body = list(stream_generation_events(failing_events()))

    assert body == [
        sse_event(
            "error",
            {
                "code": "provider_call_failed",
                "message": "The model request failed.",
                "status_code": 502,
                "conversation_id": "conversation-1",
                "assistant_message_id": "assistant-message-1",
            },
        )
    ]
    assert "private provider detail" not in body[0]


@pytest.mark.parametrize(
    "detail",
    [
        "private detail",
        123,
        {"code": 123, "message": "private detail"},
        {"code": "private_code", "message": None},
    ],
)
def test_http_error_payload_never_reflects_malformed_detail(detail: object) -> None:
    from app.api.sse import http_error_payload

    error = HTTPException(status_code=418, detail=detail)  # type: ignore[arg-type]

    assert http_error_payload(error) == {
        "code": "request_failed",
        "message": "Request failed.",
        "status_code": 418,
    }


@pytest.mark.parametrize(
    "disconnect_error",
    [OSError("client disconnected"), asyncio.CancelledError()],
)
def test_generation_response_closes_events_when_client_send_fails(
    disconnect_error: BaseException,
) -> None:
    from app.api.sse import GenerationStreamingResponse

    class TrackingEvents:
        def __init__(self) -> None:
            self.close_calls = 0
            self.yielded = False

        def __iter__(self):
            return self

        def __next__(self) -> GenerationEvent:
            if self.yielded:
                raise StopIteration
            self.yielded = True
            return GenerationEvent(event="delta", data={"text": "partial"})

        def close(self) -> None:
            self.close_calls += 1

    events = TrackingEvents()
    response = GenerationStreamingResponse(events)

    async def failing_send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            raise disconnect_error

    with pytest.raises(type(disconnect_error)):
        asyncio.run(response.stream_response(failing_send))  # type: ignore[arg-type]

    assert events.close_calls == 1


def test_generation_response_closes_events_once_after_normal_completion() -> None:
    from app.api.sse import GenerationStreamingResponse

    class TrackingEvents:
        def __init__(self) -> None:
            self.close_calls = 0
            self.remaining = [
                GenerationEvent(event="delta", data={"text": "answer"}),
                GenerationEvent(event="result", data={"conversation_id": "c-1"}),
            ]

        def __iter__(self):
            return self

        def __next__(self) -> GenerationEvent:
            if not self.remaining:
                raise StopIteration
            return self.remaining.pop(0)

        def close(self) -> None:
            self.close_calls += 1

    events = TrackingEvents()
    response = GenerationStreamingResponse(events)
    sent: list[dict[str, object]] = []

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    asyncio.run(response.stream_response(send))  # type: ignore[arg-type]

    assert events.close_calls == 1
    assert sent[-1] == {
        "type": "http.response.body",
        "body": b"",
        "more_body": False,
    }


@pytest.mark.parametrize(
    "close_error",
    [RuntimeError("private close detail"), asyncio.CancelledError("close cancelled")],
)
def test_generation_response_close_failure_does_not_mask_disconnect(
    caplog: pytest.LogCaptureFixture,
    close_error: BaseException,
) -> None:
    from app.api.sse import GenerationStreamingResponse

    class CloseFailingEvents:
        def __init__(self) -> None:
            self.close_calls = 0

        def __iter__(self):
            return self

        def __next__(self) -> GenerationEvent:
            return GenerationEvent(event="delta", data={"text": "partial"})

        def close(self) -> None:
            self.close_calls += 1
            raise close_error

    events = CloseFailingEvents()
    response = GenerationStreamingResponse(events)

    async def failing_send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    with caplog.at_level(logging.WARNING, logger="app.api.sse"):
        with pytest.raises(OSError, match="client disconnected"):
            asyncio.run(response.stream_response(failing_send))  # type: ignore[arg-type]

    assert events.close_calls == 1
    record = next(
        item
        for item in caplog.records
        if item.getMessage() == "generation_stream_close_failed"
    )
    assert record.exception_type == type(close_error).__name__
    assert record.error_code == "generation_stream_close_failed"
    assert "private close detail" not in caplog.text
    assert "close cancelled" not in caplog.text


def test_asgi_disconnect_waits_only_for_configured_provider_timeout_and_cleans_up(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    from app.api.sse import GenerationStreamingResponse

    started = threading.Event()
    finished = threading.Event()

    class TimedStream:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def __iter__(self):
            return self

        def __next__(self):
            started.set()
            time.sleep(self.timeout)
            finished.set()
            raise TimeoutError("bounded provider read timeout")

    class TimedClientFactory:
        def __init__(self) -> None:
            self.timeout: float | None = None
            self.max_retries: int | None = None

        def __call__(
            self,
            *,
            api_key: str,
            base_url: str | None = None,
            timeout: float | None = None,
            max_retries: int | None = None,
        ) -> object:
            del api_key, base_url
            self.timeout = timeout
            self.max_retries = max_retries
            bounded_timeout = timeout if timeout is not None else 0.001
            completions = SimpleNamespace(
                create=lambda **_kwargs: TimedStream(bounded_timeout)
            )
            return SimpleNamespace(chat=SimpleNamespace(completions=completions))

    class TrackingEvents:
        def __init__(self, inner: Iterator[GenerationEvent]) -> None:
            self.inner = inner
            self.close_calls = 0

        def __iter__(self):
            return self

        def __next__(self) -> GenerationEvent:
            return next(self.inner)

        def close(self) -> None:
            self.close_calls += 1
            self.inner.close()

    settings = _settings(tmp_path, model_request_timeout_seconds=0.05)
    user_id, agent_id = _seed_user_and_agent(session_factory)
    client_factory = TimedClientFactory()
    model_service = ModelService(
        settings=settings,
        client_factory=client_factory,
    )
    runtime = _agent_runtime(
        settings=settings,
        store=FakeObjectStore(),
        model_service=model_service,
    )
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,
        settings=settings,
        clock=lambda: 0.0,
    )
    events = TrackingEvents(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(text="question", agent_id=agent_id),
        )
    )
    response = GenerationStreamingResponse(events)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/conversations/stream",
        "raw_path": b"/api/conversations/stream",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("test", 1),
        "server": ("test", 80),
    }

    async def receive() -> dict[str, str]:
        deadline = time.monotonic() + 1.0
        while not started.is_set():
            assert time.monotonic() < deadline
            await asyncio.sleep(0.001)
        return {"type": "http.disconnect"}

    async def send(_message: dict[str, object]) -> None:
        return None

    started_at = time.monotonic()
    asyncio.run(response(scope, receive, send))  # type: ignore[arg-type]
    elapsed = time.monotonic() - started_at

    assert client_factory.timeout == 0.05
    assert client_factory.max_retries == 0
    assert 0.04 <= elapsed < 0.5
    assert finished.is_set()
    assert events.close_calls == 1
    with session_factory() as session:
        assistant = session.scalar(
            select(models.Message).where(models.Message.role == "assistant")
        )
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert assistant.status == "failed"
        assert assistant.metadata_json["error_code"] == "provider_call_failed"
        assert conversation.status == "idle"


def test_generator_close_after_result_does_not_rewrite_completed_message(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=("answer",)),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    stream = service.stream_turn(
        user_id=user_id,
        request=ConversationSendRequest(text="question", agent_id=agent_id),
    )

    assert next(stream).event == "accepted"
    assert next(stream).event == "delta"
    assert next(stream).event == "result"
    stream.close()

    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("completed", "answer")
        assert "error_code" not in assistant.metadata_json
        assert conversation.status == "idle"


def test_success_preserves_model_markdown_whitespace_exactly(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    expected = "  ```python\nprint('x')\n```\n  "
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(
            chunks=("  ```python\n", "print('x')\n```\n  "),
        ),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    events = list(
        service.stream_turn(
            user_id=user_id,
            request=ConversationSendRequest(text="question", agent_id=agent_id),
        )
    )

    assert events[-1].data["message"]["content"] == expected  # type: ignore[index]
    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        assert assistant is not None and assistant.content == expected


def test_whitespace_only_model_output_is_failed_not_completed(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=("  ", "\n")),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    with pytest.raises(RuntimeError, match="empty model response"):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="question", agent_id=agent_id),
            )
        )

    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert assistant.status == "failed"
        assert assistant.content == "  \n"
        assert assistant.metadata_json["error_code"] == "provider_call_failed"
        assert conversation.status == "idle"


def test_failure_persistence_error_does_not_mask_original_provider_error(
    session_factory: sessionmaker[Session], tmp_path, monkeypatch, caplog
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    provider_error = RuntimeError("private provider detail")
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(error=provider_error, chunks=()),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    def fail_persistence(*_args, **_kwargs) -> None:
        raise RuntimeError("private database detail")

    monkeypatch.setattr(service, "_fail", fail_persistence)

    with pytest.raises(RuntimeError) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="secret prompt", agent_id=agent_id),
            )
        )

    assert captured.value is provider_error
    assert "generation_failure_persistence_failed" in caplog.text
    failure_record = next(
        record
        for record in caplog.records
        if record.message == "generation_failure_persistence_failed"
    )
    assert failure_record.exception_type == "RuntimeError"
    assert failure_record.error_code == "generation_failure_persistence_failed"
    assert "private provider detail" not in caplog.text
    assert "private database detail" not in caplog.text
    assert "secret prompt" not in caplog.text


def test_mark_streaming_failure_persists_received_first_chunk_and_propagates(
    session_factory: sessionmaker[Session], tmp_path, monkeypatch
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=("first chunk", "not consumed")),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )
    mark_error = RuntimeError("mark failed")
    captured_fail: dict[str, object] = {}
    original_fail = service._fail

    def fail_mark(*_args, **_kwargs) -> None:
        raise mark_error

    def capture_fail(message_id: str, **kwargs) -> None:
        captured_fail.update(kwargs)
        original_fail(message_id, **kwargs)

    monkeypatch.setattr(service, "_mark_streaming", fail_mark)
    monkeypatch.setattr(service, "_fail", capture_fail)

    with pytest.raises(RuntimeError) as captured:
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="question", agent_id=agent_id),
            )
        )

    assert captured.value is mark_error
    assert captured_fail["content"] == "first chunk"
    assert captured_fail["error_code"] == "provider_call_failed"
    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("failed", "first chunk")
        assert conversation.status == "idle"


def test_response_projection_failure_after_complete_does_not_rewrite_terminal_row(
    session_factory: sessionmaker[Session], tmp_path, monkeypatch, caplog
) -> None:
    from app.services import generation_service as generation_service_module

    user_id, agent_id = _seed_user_and_agent(session_factory)
    service = GenerationService(
        session_factory=session_factory,
        runtime=RecordingRuntime(chunks=("answer",)),  # type: ignore[arg-type]
        settings=_settings(tmp_path),
        clock=lambda: 0.0,
    )

    def fail_projection(_message):
        raise RuntimeError("private projection detail")

    monkeypatch.setattr(
        generation_service_module,
        "conversation_message_response",
        fail_projection,
    )

    with pytest.raises(RuntimeError, match="private projection detail"):
        list(
            service.stream_turn(
                user_id=user_id,
                request=ConversationSendRequest(text="question", agent_id=agent_id),
            )
        )

    with session_factory() as session:
        assistant = session.scalar(select(models.Message).where(models.Message.role == "assistant"))
        conversation = session.scalar(select(models.Conversation))
        assert assistant is not None and conversation is not None
        assert (assistant.status, assistant.content) == ("completed", "answer")
        assert "error_code" not in assistant.metadata_json
        assert conversation.status == "idle"
    assert "generation_failure_persistence_failed" not in caplog.text
    assert "private projection detail" not in caplog.text


@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
def test_repository_refuses_to_rewrite_terminal_assistant(
    session_factory: sessionmaker[Session], terminal_status: str
) -> None:
    user_id, agent_id = _seed_user_and_agent(session_factory)
    with session_scope(session_factory) as session:
        conversation = _add_conversation(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )
        assistant = _add_message(
            session,
            user_id=user_id,
            conversation_id=conversation.id,
            sequence=1,
            role="assistant",
            status=terminal_status,
            content="terminal",
        )
        assistant_id = assistant.id

    with session_factory() as session:
        with pytest.raises(ValueError, match="assistant_message_not_writable"):
            ConversationRepository().checkpoint_assistant(
                session,
                user_id=user_id,
                message_id=assistant_id,
                content="rewritten",
            )
        session.rollback()
        with pytest.raises(ValueError, match="assistant_message_not_writable"):
            ConversationRepository().finish_assistant(
                session,
                user_id=user_id,
                message_id=assistant_id,
                content="rewritten",
                status="failed" if terminal_status == "completed" else "completed",
            )

    with session_factory() as session:
        assistant = session.get(models.Message, assistant_id)
        assert assistant is not None
        assert (assistant.status, assistant.content) == (terminal_status, "terminal")


def test_recover_interrupted_is_idempotent_and_never_calls_runtime(
    session_factory: sessionmaker[Session], tmp_path
) -> None:
    with session_scope(session_factory) as session:
        user = _add_user(session)
        second_user = _add_user(session, user_id=2, username="bob")
        agent = _add_agent(session, owner_id=user.id)
        first = _add_conversation(
            session,
            user_id=user.id,
            agent_id=agent.id,
            status="generating",
        )
        second = _add_conversation(
            session,
            user_id=second_user.id,
            agent_id=agent.id,
            status="generating",
        )
        _add_message(
            session,
            user_id=user.id,
            conversation_id=first.id,
            sequence=1,
            role="assistant",
            status="pending",
            content="",
            metadata={"kept": True},
        )
        _add_message(
            session,
            user_id=second_user.id,
            conversation_id=second.id,
            sequence=1,
            role="assistant",
            status="streaming",
            content="partial",
        )
    runtime = RecordingRuntime()
    service = GenerationService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=_settings(tmp_path),
    )

    assert service.recover_interrupted() == 2
    assert service.recover_interrupted() == 0
    assert runtime.calls == []
    with session_factory() as session:
        messages = list(session.scalars(select(models.Message)))
        conversations = list(session.scalars(select(models.Conversation)))
        assert all(message.status == "failed" for message in messages)
        assert all(
            message.metadata_json["error_code"] == "generation_interrupted" for message in messages
        )
        assert messages[0].metadata_json["kept"] is True
        assert all(conversation.status == "idle" for conversation in conversations)
