from __future__ import annotations

from collections.abc import Callable, Iterator
import threading

from fastapi import HTTPException
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db import models
from app.db.session import session_scope
from app.services.json_safety import MAX_JSON_STRING_CHARS
from app.repositories.subtask_context_repository import SubtaskContextRepository
from app.schemas.agents import AgentConfig
from app.schemas.chat import ChatSendRequest
from app.schemas.modeling import ModelRef
from app.services.agent_runtime import RuntimeTurn
from app.services.runtime_types import (
    AssistantMessageEvent,
    ProviderCallMetrics,
    RuntimeEvent,
    RuntimeImageContext,
    RuntimeSelectedDocumentsContext,
    RuntimeTextContext,
    TextDeltaEvent,
    ToolCall,
    ToolResult,
    ToolResultEvent,
    ToolStartEvent,
)
from app.services.task_execution_service import (
    PreparedTaskExecution,
    TaskExecutionService,
)
from app.services import task_execution_service as task_execution_module
from app.services.runtime_context_projection import TaskExecutionError
from app.services.task_execution_cancellation import TaskExecutionState


class StubRuntime:
    def __init__(
        self,
        events: tuple[RuntimeEvent, ...] = (),
        *,
        error: Exception | None = None,
        metrics: tuple[ProviderCallMetrics, ...] = (),
    ) -> None:
        self.events = events
        self.error = error
        self.metrics = metrics
        self.prepare_calls: list[RuntimeTurn] = []

    def prepare_turn(self, turn: RuntimeTurn):
        self.prepare_calls.append(turn)
        return turn

    def stream_turn(
        self,
        turn,
        *,
        observer: Callable[[ProviderCallMetrics], None],
    ) -> Iterator[RuntimeEvent]:
        del turn
        for metrics in self.metrics:
            observer(metrics)
        yield from self.events
        if self.error is not None:
            raise self.error


class _CloseFailure(BaseException):
    pass


class _CloseRaisingIterator:
    def __init__(self, events: tuple[RuntimeEvent, ...]) -> None:
        self._events = iter(events)

    def __iter__(self):
        return self

    def __next__(self) -> RuntimeEvent:
        return next(self._events)

    def close(self) -> None:
        raise _CloseFailure("must not escape")


class CloseRaisingRuntime(StubRuntime):
    def stream_turn(self, turn, *, observer):
        del turn, observer
        return _CloseRaisingIterator(self.events)


class BlockingNextIterator:
    def __init__(
        self,
        *,
        entered: threading.Event,
        release: threading.Event,
    ) -> None:
        self.entered = entered
        self.release = release
        self._returned = False

    def __iter__(self):
        return self

    def __next__(self) -> RuntimeEvent:
        if self._returned:
            raise StopIteration
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise RuntimeError("blocking iterator timed out")
        self._returned = True
        return TextDeltaEvent(content="must not be reduced")

    def close(self) -> None:
        return None


class BlockingNextRuntime(StubRuntime):
    def __init__(self, *, entered: threading.Event, release: threading.Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release

    def stream_turn(self, turn, *, observer):
        del turn, observer
        return BlockingNextIterator(entered=self.entered, release=self.release)


@pytest.fixture
def session_factory(tmp_path) -> Iterator[sessionmaker[Session]]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'task-execution.db'}",
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


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'task-execution.db'}",
        qwen_api_key="test-key",
        qwen_chat_models="qwen3.7-plus,qwen3.7-max",
        default_chat_provider="qwen",
        chat_context_token_budget=16_000,
        tool_result_token_reserve=4_096,
        image_input_token_reserve=4_096,
    )


@pytest.fixture
def user_id(session_factory: sessionmaker[Session]) -> int:
    with session_scope(session_factory) as session:
        user = _add_user(session, user_id=1, username="alice")
        return user.id


def _add_user(
    session: Session,
    *,
    user_id: int,
    username: str,
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
    agent_id: str,
    user_id: int,
    default_model: ModelRef | None = None,
) -> models.Resource:
    agent = models.Resource(
        id=agent_id,
        user_id=user_id,
        resource_type="agent",
        name=agent_id,
        config_json=AgentConfig(
            system_prompt="Answer precisely.",
            default_model=default_model,
        ).model_dump(mode="json"),
    )
    session.add(agent)
    session.flush()
    return agent


def _complete_events() -> tuple[RuntimeEvent, ...]:
    call = ToolCall(id="call-1", name="lookup", arguments={"query": "weather"})
    result = ToolResult(
        call_id=call.id,
        content='{"temperature":20}',
        metadata={
            "sources": [{"document_id": "doc-1"}],
            "termination_reason": "stop",
        },
    )
    return (
        AssistantMessageEvent(
            content="",
            tool_calls=(call,),
            provider="qwen",
            model="qwen3.7-plus",
        ),
        ToolStartEvent(call=call),
        ToolResultEvent(call=call, result=result),
        TextDeltaEvent(content="final"),
        AssistantMessageEvent(
            content="final",
            provider="qwen",
            model="qwen3.7-plus",
        ),
    )


def _metrics() -> ProviderCallMetrics:
    return ProviderCallMetrics(
        call_index=1,
        provider="qwen",
        model="qwen3.7-plus",
        provider_request_id="provider-request-1",
        input_tokens=12,
        output_tokens=5,
        first_token_latency_ms=10.0,
        duration_ms=20.0,
        status="completed",
        unavailable_fields=(),
    )


def _service(
    *,
    session_factory: sessionmaker[Session],
    settings: Settings,
    runtime: StubRuntime,
    metrics_observer=None,
) -> TaskExecutionService:
    return TaskExecutionService(
        session_factory=session_factory,
        runtime=runtime,  # type: ignore[arg-type]
        settings=settings,
        metrics_observer=metrics_observer,
    )


def _error_code(error: HTTPException) -> str:
    assert isinstance(error.detail, dict)
    code = error.detail.get("code")
    assert isinstance(code, str)
    return code


def test_chat_send_request_validates_message_and_unique_contexts() -> None:
    assert ChatSendRequest(message="x").context_ids == []
    with pytest.raises(ValidationError):
        ChatSendRequest(message="")
    with pytest.raises(ValidationError):
        ChatSendRequest(message="x", context_ids=[1, 1])
    with pytest.raises(ValidationError):
        ChatSendRequest(message="x", context_ids=list(range(1, 12)))


def test_execute_persists_complete_chain_and_ordered_blocks(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    runtime = StubRuntime(_complete_events())
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=runtime,
    )

    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="search"),
    )
    events = list(service.execute(prepared))

    assert events[0].type == "start"
    assert events[-1].type == "done"
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        task = session.get(models.Task, prepared.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "COMPLETED"
        assert task.status == "COMPLETED"
        assert assistant.result is not None
        assert assistant.result["value"] == "final"
        assert set(assistant.result) == {
            "value",
            "messages_chain",
            "blocks",
            "context_compactions",
            "sources",
            "termination_reason",
        }
        assert [
            item["role"] for item in assistant.result["messages_chain"]
        ] == ["assistant", "tool", "assistant"]
        assert assistant.result["messages_chain"][-1]["model_info"] == {
            "provider": "qwen",
            "model": "qwen3.7-plus",
        }
        blocks = assistant.result["blocks"]
        assert isinstance(blocks, list)
        assert [block["type"] for block in blocks] == ["tool", "text"]
        assert blocks[0]["status"] == "done"
        assert blocks[1]["content"] == "final"
        assert assistant.result["sources"] == [{"document_id": "doc-1"}]
        assert assistant.result["termination_reason"] == "stop"


def test_prepare_send_creates_or_reuses_task_and_locks_agent_and_model_rules(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    with session_scope(session_factory) as session:
        _add_agent(
            session,
            agent_id="agent-1",
            user_id=user_id,
            default_model=ModelRef(provider="qwen", model="qwen3.7-max"),
        )
        _add_agent(session, agent_id="agent-2", user_id=user_id)
    runtime = StubRuntime(_complete_events())
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=runtime,
    )

    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(
            message="first",
            agent_id="agent-1",
            model_override=ModelRef(provider="qwen", model="qwen3.7-plus"),
        ),
    )
    assert prepared.model == "qwen3.7-plus"
    assert prepared.user_message_id == 1

    with pytest.raises(HTTPException) as running:
        service.prepare_send(
            user_id=user_id,
            request=ChatSendRequest(task_id=prepared.task_id, message="second"),
        )
    assert running.value.status_code == 409
    assert _error_code(running.value) == "task_running"

    list(service.execute(prepared))
    with pytest.raises(HTTPException) as changed_agent:
        service.prepare_send(
            user_id=user_id,
            request=ChatSendRequest(
                task_id=prepared.task_id,
                message="second",
                agent_id="agent-2",
            ),
        )
    assert _error_code(changed_agent.value) == "agent_locked"
    with pytest.raises(HTTPException) as changed_model:
        service.prepare_send(
            user_id=user_id,
            request=ChatSendRequest(
                task_id=prepared.task_id,
                message="second",
                model_override=ModelRef(provider="qwen", model="qwen3.7-max"),
            ),
        )
    assert _error_code(changed_model.value) == "model_override_not_allowed"

    continued = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(
            task_id=prepared.task_id,
            message="second",
            agent_id="agent-1",
        ),
    )
    assert continued.task_id == prepared.task_id
    assert continued.user_message_id == 3
    assert continued.model == "qwen3.7-plus"
    assert runtime.prepare_calls[-1].turns[-1].user.text == "second"
    assert runtime.prepare_calls[-1].turns[-2].assistants[-1].text == "final"


def test_prepare_send_context_binding_is_owner_scoped_and_atomic(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    with session_scope(session_factory) as session:
        _add_user(session, user_id=2, username="bob")
        repository = SubtaskContextRepository()
        owned = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="owned.txt",
            status="ready",
            extracted_text="owned context",
        )
        other = repository.create_draft(
            session,
            user_id=2,
            context_type="attachment",
            name="other.txt",
            status="ready",
        )
        owned_id = owned.id
        other_id = other.id
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )

    with pytest.raises(HTTPException) as captured:
        service.prepare_send(
            user_id=user_id,
            request=ChatSendRequest(
                message="should roll back",
                context_ids=[owned_id, other_id],
            ),
        )
    assert _error_code(captured.value) == "context_not_ready"
    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(models.Task.id))) == 0
        assert session.scalar(select(func.count(models.Subtask.id))) == 0
        assert session.get(models.SubtaskContext, owned_id).subtask_id == 0

    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="valid", context_ids=[owned_id]),
    )
    with session_scope(session_factory) as session:
        assert (
            session.get(models.SubtaskContext, owned_id).subtask_id
            == prepared.user_subtask_id
        )
        assert session.get(models.SubtaskContext, other_id).subtask_id == 0


def test_runtime_batch_loads_mysql_contexts_and_never_replays_selection(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    with session_scope(session_factory) as session:
        repository = SubtaskContextRepository()
        attachment = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="notes.txt",
            status="ready",
            binary_data=b"must-not-enter-runtime",
            extracted_text="attachment text",
        )
        image = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="chart.png",
            status="ready",
            binary_data=b"image-bytes-must-not-enter-runtime",
            image_base64="aW1hZ2U=",
            mime_type="image/png",
        )
        knowledge = repository.create_draft(
            session,
            user_id=user_id,
            context_type="knowledge_base",
            name="retrieval",
            status="ready",
            extracted_text="knowledge text",
        )
        selected = repository.create_draft(
            session,
            user_id=user_id,
            context_type="selected_documents",
            name="selection",
            status="ready",
            type_data={
                "knowledge_id": "knowledge-1",
                "document_ids": ["document-2", "document-1"],
            },
        )
        context_ids = [attachment.id, image.id, knowledge.id, selected.id]
    runtime = StubRuntime(_complete_events())
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=runtime,
    )

    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="with contexts", context_ids=context_ids),
    )
    current_contexts = runtime.prepare_calls[-1].turns[-1].user.contexts
    assert [type(context) for context in current_contexts] == [
        RuntimeTextContext,
        RuntimeImageContext,
        RuntimeTextContext,
        RuntimeSelectedDocumentsContext,
    ]
    assert current_contexts[0].text == "attachment text"
    assert current_contexts[1].image_base64 == "aW1hZ2U="
    assert current_contexts[2].text == "knowledge text"
    assert current_contexts[3].document_ids == ("document-2", "document-1")
    assert "must-not-enter-runtime" not in str(current_contexts)

    list(service.execute(prepared))
    continued = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(task_id=prepared.task_id, message="next"),
    )
    del continued
    historical_contexts = runtime.prepare_calls[-1].turns[0].user.contexts
    assert [type(context) for context in historical_contexts] == [
        RuntimeTextContext,
        RuntimeImageContext,
        RuntimeTextContext,
    ]
    assert all(
        not isinstance(context, RuntimeSelectedDocumentsContext)
        for context in historical_contexts
    )
    assert runtime.prepare_calls[-1].turns[-1].user.contexts == ()


def test_malformed_contexts_raise_stable_execution_errors_and_roll_back_send(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    with session_scope(session_factory) as session:
        repository = SubtaskContextRepository()
        invalid_mime = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="vector.svg",
            status="ready",
            image_base64="aW1hZ2U=",
            mime_type="image/svg+xml",
        )
        invalid_base64 = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="broken.png",
            status="ready",
            image_base64="not valid%%%",
            mime_type="image/png",
        )
        noncanonical_base64 = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="noncanonical.png",
            status="ready",
            image_base64="Zh==",
            mime_type="image/png",
        )
        invalid_padding = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="missing-padding.png",
            status="ready",
            image_base64="aW1hZ2U",
            mime_type="image/png",
        )
        invalid_reference = repository.create_draft(
            session,
            user_id=user_id,
            context_type="selected_documents",
            name="invalid selection",
            status="ready",
            type_data={"knowledge_id": "knowledge-1", "document_ids": "bad"},
        )
        too_large = repository.create_draft(
            session,
            user_id=user_id,
            context_type="attachment",
            name="huge.txt",
            status="ready",
            extracted_text="x" * (MAX_JSON_STRING_CHARS + 1),
        )
        cases = (
            (invalid_mime.id, "context_invalid"),
            (invalid_base64.id, "context_invalid"),
            (noncanonical_base64.id, "context_invalid"),
            (invalid_padding.id, "context_invalid"),
            (invalid_reference.id, "context_invalid"),
            (too_large.id, "context_too_large"),
        )
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )

    for context_id, code in cases:
        with pytest.raises(TaskExecutionError) as captured:
            service.prepare_send(
                user_id=user_id,
                request=ChatSendRequest(
                    message="invalid context",
                    context_ids=[context_id],
                ),
            )
        assert captured.value.code == code

    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count(models.Task.id))) == 0
        assert session.scalar(select(func.count(models.Subtask.id))) == 0


def test_failure_and_invalid_chain_persist_safe_failed_results(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    failure_service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(
            (TextDeltaEvent(content="partial"),),
            error=RuntimeError("secret provider exception"),
        ),
    )
    failed = failure_service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="fail"),
    )
    failure_events = list(failure_service.execute(failed))
    assert failure_events[-1].type == "error"

    call = ToolCall(id="dangling", name="lookup", arguments={})
    invalid_service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(
            (
                AssistantMessageEvent(content="", tool_calls=(call,)),
                ToolStartEvent(call=call),
            )
        ),
    )
    invalid = invalid_service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="invalid chain"),
    )
    invalid_events = list(invalid_service.execute(invalid))
    assert invalid_events[-1].data["code"] == "runtime_output_invalid"

    with session_scope(session_factory) as session:
        failed_row = session.get(models.Subtask, failed.assistant_subtask_id)
        invalid_row = session.get(models.Subtask, invalid.assistant_subtask_id)
        assert failed_row is not None
        assert invalid_row is not None
        assert failed_row.status == "FAILED"
        assert failed_row.result["value"] == "partial"
        assert set(failed_row.result) == {
            "value",
            "blocks",
            "context_compactions",
            "sources",
            "termination_reason",
        }
        assert "messages_chain" not in failed_row.result
        assert failed_row.error_message == "provider_call_failed"
        assert "secret" not in str(failed_row.result)
        assert invalid_row.status == "FAILED"
        assert invalid_row.error_message == "runtime_output_invalid"


@pytest.mark.parametrize(
    "events",
    [
        (AssistantMessageEvent(content=""),),
        (AssistantMessageEvent(content=[{"type": "image", "url": "private"}]),),
        (
            AssistantMessageEvent(
                content="",
                tool_calls=(ToolCall(id="call-eof", name="lookup", arguments={}),),
            ),
        ),
        (
            AssistantMessageEvent(
                content="",
                tool_calls=(ToolCall(id="call-result", name="lookup", arguments={}),),
            ),
            ToolStartEvent(
                call=ToolCall(id="call-result", name="lookup", arguments={})
            ),
            ToolResultEvent(
                call=ToolCall(id="call-result", name="lookup", arguments={}),
                result=ToolResult(call_id="call-result", content="result"),
            ),
        ),
    ],
)
def test_invalid_success_terminal_shape_is_a_stable_execution_failure(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    events: tuple[RuntimeEvent, ...],
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(events),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="invalid terminal"),
    )

    execution_events = list(service.execute(prepared))

    assert execution_events[-1].type == "error"
    assert execution_events[-1].data["code"] == "runtime_output_invalid"
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        task = session.get(models.Task, prepared.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "FAILED"
        assert assistant.error_message == "runtime_output_invalid"
        assert task.status == "FAILED"


def test_cancel_persists_partial_output_and_cancelled_status(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(
            (
                TextDeltaEvent(content="part"),
                TextDeltaEvent(content="never consumed"),
            )
        ),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="cancel"),
    )
    execution = service.execute(prepared)
    assert next(execution).type == "start"
    assert next(execution).type == "block_created"
    assert next(execution).type == "chunk"
    assert service.cancel(user_id=user_id, task_id=prepared.task_id)
    remaining = list(execution)
    assert remaining[-1].type == "cancelled"

    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        task = session.get(models.Task, prepared.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "CANCELLED"
        assert task.status == "CANCELLED"
        assert assistant.result["value"] == "part"
        assert set(assistant.result) == {
            "value",
            "blocks",
            "context_compactions",
            "sources",
            "termination_reason",
        }


def test_generator_close_after_running_persists_generation_closed_failure(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="close generator"),
    )
    execution = service.execute(prepared)
    assert next(execution).type == "start"

    execution.close()

    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        task = session.get(models.Task, prepared.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "FAILED"
        assert assistant.error_message == "generation_closed"
        assert task.status == "FAILED"


def test_cancel_before_start_wins_once_and_cancel_before_completion_beats_done(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    before_start = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="cancel before start"),
    )
    assert service.cancel(user_id=user_id, task_id=before_start.task_id)
    assert not service.cancel(user_id=user_id, task_id=before_start.task_id)
    assert [event.type for event in service.execute(before_start)] == ["cancelled"]

    racing = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="cancel before terminal claim"),
    )
    execution = service.execute(racing)
    while True:
        event = next(execution)
        block = event.data.get("block")
        if (
            event.type == "block_updated"
            and isinstance(block, dict)
            and block.get("type") == "text"
        ):
            break
    assert service.cancel(user_id=user_id, task_id=racing.task_id)
    assert [event.type for event in execution] == ["cancelled"]
    assert not service.cancel(user_id=user_id, task_id=racing.task_id)


def test_cancel_after_completion_loses_and_close_failure_cannot_mask_done(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr(
        task_execution_module.logger,
        "warning",
        lambda message, **_kwargs: warnings.append(message),
    )
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=CloseRaisingRuntime(_complete_events()),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="complete despite close"),
    )

    events = list(service.execute(prepared))

    assert events[-1].type == "done"
    assert "runtime_stream_close_failed" in warnings
    assert not service.cancel(user_id=user_id, task_id=prepared.task_id)


@pytest.mark.parametrize("cancelled", [False, True])
def test_terminal_persistence_failure_falls_back_to_failed(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
    cancelled: bool,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="persist fallback"),
    )
    execution = service.execute(prepared)
    if cancelled:
        assert next(execution).type == "start"
        monkeypatch.setattr(
            service.repository,
            "cancel_assistant",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("private cancellation persistence failure")
            ),
        )
        assert service.cancel(user_id=user_id, task_id=prepared.task_id)
        events = list(execution)
    else:
        monkeypatch.setattr(
            service.repository,
            "finish_assistant",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("private completion persistence failure")
            ),
        )
        events = list(execution)

    assert events[-1].type == "error"
    assert events[-1].data["code"] == "generation_persistence_failed"
    assert "private" not in str(events[-1].data)
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        task = session.get(models.Task, prepared.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "FAILED"
        assert assistant.error_message == "generation_persistence_failed"
        assert set(assistant.result) == {
            "value",
            "blocks",
            "context_compactions",
            "sources",
            "termination_reason",
        }
        assert task.status == "FAILED"


def test_ambiguous_terminal_commit_is_not_overwritten_by_fallback(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="ambiguous commit"),
    )
    original = service.repository.finish_assistant

    def commit_then_raise(_session: Session, **kwargs: object) -> None:
        with session_scope(session_factory) as committed_session:
            original(committed_session, **kwargs)
        raise RuntimeError("private ambiguous commit failure")

    monkeypatch.setattr(service.repository, "finish_assistant", commit_then_raise)

    events = list(service.execute(prepared))

    assert events[-1].type == "error"
    assert events[-1].data["code"] == "generation_persistence_failed"
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        task = session.get(models.Task, prepared.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "COMPLETED"
        assert assistant.error_message is None
        assert assistant.result["value"] == "final"
        assert task.status == "COMPLETED"


def test_terminal_persistence_fallback_failure_re_raises_and_cleans_registry(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="fallback also fails"),
    )
    monkeypatch.setattr(
        service.repository,
        "finish_assistant",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private original persistence failure")
        ),
    )
    monkeypatch.setattr(
        service.repository,
        "fail_active_assistant",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("private fallback persistence failure")
        ),
    )
    logged: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        task_execution_module.logger,
        "error",
        lambda message, *, extra, **_kwargs: logged.append((message, extra)),
    )

    with pytest.raises(RuntimeError, match="private original persistence failure"):
        list(service.execute(prepared))

    assert [message for message, _extra in logged] == [
        "generation_terminal_persistence_failed",
        "generation_persistence_fallback_failed",
    ]
    assert logged[0][1] == {
        "exception_type": "RuntimeError",
        "error_code": "generation_persistence_failed",
    }
    assert "private" not in str(logged)
    assert not service.cancel(user_id=user_id, task_id=prepared.task_id)
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        assert assistant is not None
        assert assistant.status == "RUNNING"


def test_threaded_cancel_wins_while_next_is_blocked_and_only_once(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    runtime = BlockingNextRuntime(entered=entered, release=release)
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=runtime,
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="threaded blocked next"),
    )
    events: list[object] = []
    errors: list[BaseException] = []

    def run_execution() -> None:
        try:
            events.extend(service.execute(prepared))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run_execution)
    worker.start()
    assert entered.wait(timeout=5)
    assert service.cancel(user_id=user_id, task_id=prepared.task_id)
    assert not service.cancel(user_id=user_id, task_id=prepared.task_id)
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    assert [event.type for event in events] == ["start", "cancelled"]
    assert not service.cancel(user_id=user_id, task_id=prepared.task_id)
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        assert assistant is not None
        assert assistant.status == "CANCELLED"


def test_threaded_cancel_wins_before_terminal_claim(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="threaded terminal claim"),
    )
    barrier = threading.Barrier(2)
    release = threading.Event()
    original_claim = TaskExecutionState.claim_terminal

    def delayed_claim(
        state: TaskExecutionState,
        desired,
    ):
        barrier.wait(timeout=5)
        if not release.wait(timeout=5):
            raise RuntimeError("terminal claim timed out")
        return original_claim(state, desired)

    monkeypatch.setattr(TaskExecutionState, "claim_terminal", delayed_claim)
    events: list[object] = []
    errors: list[BaseException] = []

    def run_execution() -> None:
        try:
            events.extend(service.execute(prepared))
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=run_execution)
    worker.start()
    barrier.wait(timeout=5)
    assert service.cancel(user_id=user_id, task_id=prepared.task_id)
    release.set()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert errors == []
    assert events[-1].type == "cancelled"
    assert all(event.type != "done" for event in events)
    assert not service.cancel(user_id=user_id, task_id=prepared.task_id)
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, prepared.assistant_subtask_id)
        assert assistant is not None
        assert assistant.status == "CANCELLED"


def test_metrics_observer_receives_events_and_callback_failure_is_nonfatal(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[ProviderCallMetrics] = []
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events(), metrics=(_metrics(),)),
        metrics_observer=received.append,
    )
    prepared = service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="metrics"),
    )
    assert list(service.execute(prepared))[-1].type == "done"
    assert received == [_metrics()]

    def broken_observer(_metrics: ProviderCallMetrics) -> None:
        raise RuntimeError("private observer failure")

    failing_callback = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events(), metrics=(_metrics(),)),
        metrics_observer=broken_observer,
    )
    prepared = failing_callback.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="metrics callback failure"),
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        task_execution_module.logger,
        "warning",
        lambda message, **_kwargs: warnings.append(message),
    )
    assert list(failing_callback.execute(prepared))[-1].type == "done"
    assert "provider_metrics_observer_failed" in warnings

    default_logging = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events(), metrics=(_metrics(),)),
    )
    prepared = default_logging.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="default metrics logging"),
    )
    records: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        task_execution_module.logger,
        "info",
        lambda message, *, extra: records.append((message, extra)),
    )
    assert list(default_logging.execute(prepared))[-1].type == "done"
    message, extra = records[-1]
    assert message == "provider_call_metrics"
    assert extra["provider_status"] == "completed"
    assert extra["input_tokens"] == 12
    assert extra["output_tokens"] == 5


def test_retry_reuses_failed_assistant_and_recovery_marks_interrupted(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    failure_service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(error=RuntimeError("boom")),
    )
    failed = failure_service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="retry me"),
    )
    list(failure_service.execute(failed))

    retry_service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    retried = retry_service.prepare_retry(
        user_id=user_id,
        task_id=failed.task_id,
        subtask_id=failed.assistant_subtask_id,
    )
    assert retried.assistant_subtask_id == failed.assistant_subtask_id
    assert retried.user_subtask_id is None
    with session_scope(session_factory) as session:
        assert session.get(models.Subtask, retried.assistant_subtask_id).result is None

    list(retry_service.execute(retried))
    pending = retry_service.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="interrupted"),
    )
    assert retry_service.recover_interrupted() == 1
    with session_scope(session_factory) as session:
        assistant = session.get(models.Subtask, pending.assistant_subtask_id)
        task = session.get(models.Task, pending.task_id)
        assert assistant is not None
        assert task is not None
        assert assistant.status == "FAILED"
        assert assistant.error_message == "generation_interrupted"
        assert task.status == "FAILED"


def test_retry_older_failure_cuts_off_all_future_turns_without_new_rows(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    failing = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(error=RuntimeError("boom")),
    )
    older = failing.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(message="older prompt"),
    )
    list(failing.execute(older))

    completing = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )
    later = completing.prepare_send(
        user_id=user_id,
        request=ChatSendRequest(task_id=older.task_id, message="future prompt"),
    )
    list(completing.execute(later))
    with session_scope(session_factory) as session:
        row_count = session.scalar(
            select(func.count(models.Subtask.id)).where(
                models.Subtask.task_id == older.task_id
            )
        )
    assert row_count == 4

    retry_runtime = StubRuntime(_complete_events())
    retrying = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=retry_runtime,
    )
    retried = retrying.prepare_retry(
        user_id=user_id,
        task_id=older.task_id,
        subtask_id=older.assistant_subtask_id,
    )

    assert retried.assistant_subtask_id == older.assistant_subtask_id
    assert [turn.user.text for turn in retry_runtime.prepare_calls[-1].turns] == [
        "older prompt"
    ]
    assert retry_runtime.prepare_calls[-1].turns[0].assistants == ()
    assert "future prompt" not in str(retry_runtime.prepare_calls[-1].turns)
    assert "final" not in str(retry_runtime.prepare_calls[-1].turns)
    with session_scope(session_factory) as session:
        assert session.scalar(
            select(func.count(models.Subtask.id)).where(
                models.Subtask.task_id == older.task_id
            )
        ) == row_count


def test_history_rejects_assistant_parent_mismatch_without_attaching_wrong_turn(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    with session_scope(session_factory) as session:
        task = models.Task(user_id=user_id, name="malformed", status="COMPLETED")
        session.add(task)
        session.flush()
        session.add_all(
            [
                models.Subtask(
                    user_id=user_id,
                    task_id=task.id,
                    role="USER",
                    message_id=1,
                    prompt="first user",
                    status="COMPLETED",
                    progress=100,
                ),
                models.Subtask(
                    user_id=user_id,
                    task_id=task.id,
                    role="ASSISTANT",
                    message_id=2,
                    parent_id=999,
                    status="COMPLETED",
                    progress=100,
                    result={"value": "must not attach"},
                ),
            ]
        )
        session.flush()
        task_id = task.id
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )

    with pytest.raises(TaskExecutionError) as captured:
        service.prepare_send(
            user_id=user_id,
            request=ChatSendRequest(task_id=task_id, message="current user"),
        )

    assert captured.value.code == "history_invalid"
    with session_scope(session_factory) as session:
        rows = session.scalars(
            select(models.Subtask).where(models.Subtask.task_id == task_id)
        ).all()
        assert len(rows) == 2
        assert session.get(models.Task, task_id).status == "COMPLETED"


def test_prepare_send_rejects_other_owner_task(
    session_factory: sessionmaker[Session],
    settings: Settings,
    user_id: int,
) -> None:
    with session_scope(session_factory) as session:
        _add_user(session, user_id=2, username="bob")
        task = models.Task(user_id=2, name="private", status="COMPLETED")
        session.add(task)
        session.flush()
        task_id = task.id
    service = _service(
        session_factory=session_factory,
        settings=settings,
        runtime=StubRuntime(_complete_events()),
    )

    with pytest.raises(HTTPException) as captured:
        service.prepare_send(
            user_id=user_id,
            request=ChatSendRequest(task_id=task_id, message="intrude"),
        )
    assert captured.value.status_code == 404
    assert _error_code(captured.value) == "task_not_found"


def test_prepared_execution_is_frozen() -> None:
    assert PreparedTaskExecution.__dataclass_params__.frozen
