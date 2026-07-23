from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from functools import wraps
import threading
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, call

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import socketio

from app.api.ws import chat_namespace as chat_namespace_module
from app.api.ws.chat_namespace import ChatNamespace
from app.api.ws.emitter import WebSocketChatEmitter
from app.core.auth import create_access_token
from app.db import models
from app.db.session import session_scope
from app.schemas.socket_events import TaskBriefPayload
from app.services.chat_blocks import append_text_block, create_text_block
from app.services.chat_stream_store import (
    ActiveStreamSnapshot,
    ChatStreamStaleGeneration,
    MemoryChatStreamStore,
)
from app.services.task_execution_service import (
    PreparedTaskExecution,
    TaskExecutionEvent,
)
from app.services.task_service import TaskServiceError
from app.schemas.tasks import SubtaskResponse


def run_async(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapper


@pytest.fixture
def namespace() -> ChatNamespace:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.User.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    task_service = Mock()
    execution = Mock()
    stream_store = AsyncMock()
    emitter = AsyncMock()
    enter_room = AsyncMock()
    leave_room = AsyncMock()
    scheduler = Mock()
    value = ChatNamespace(
        task_service=task_service,
        execution=execution,
        stream_store=stream_store,
        emitter=emitter,
        session_factory=factory,
        enter_room=enter_room,
        leave_room=leave_room,
        schedule_execution=scheduler,
    )
    value.get_session = AsyncMock(return_value={"user_id": 3})  # type: ignore[method-assign]
    value._test_engine = engine  # type: ignore[attr-defined]
    yield value
    engine.dispose()


def _prepared(*, retry: bool = False) -> PreparedTaskExecution:
    return PreparedTaskExecution(
        task_id=7,
        user_subtask_id=None if retry else 10,
        user_message_id=None if retry else 1,
        assistant_subtask_id=11,
        runtime_turn=SimpleNamespace(context=SimpleNamespace(user_id=3)),
        provider="test",
        model="model",
    )


def _subtask_dump() -> SubtaskResponse:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    return SubtaskResponse(
        id=11,
        task_id=7,
        role="ASSISTANT",
        message_id=5,
        parent_id=4,
        prompt="",
        status="COMPLETED",
        progress=100,
        result={"value": "answer", "messages_chain": []},
        error_message=None,
        contexts=[],
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _brief(status: str = "RUNNING") -> TaskBriefPayload:
    return TaskBriefPayload(
        id=7,
        name="Task",
        href="/chat?task=7",
        status=status,
        agent={"id": None, "name": "No agent", "is_available": True},
        model_override=None,
        created_at="2026-07-22T00:00:00Z",
        updated_at="2026-07-22T00:00:01Z",
    )


class LifecycleExecution:
    def __init__(self, events: list[TaskExecutionEvent] | None = None) -> None:
        self.events = events or []
        self.status = "PENDING"
        self.provider_called = False
        self.request_cancel = Mock(return_value=True)

    def execute(self, _prepared: PreparedTaskExecution):
        self.status = "RUNNING"
        try:
            yield TaskExecutionEvent(
                type="start",
                data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
            )
            self.provider_called = True
            for event in self.events:
                if event.type == "done":
                    self.status = "COMPLETED"
                elif event.type == "error":
                    self.status = "FAILED"
                elif event.type == "cancelled":
                    self.status = "CANCELLED"
                yield event
        finally:
            if self.status == "RUNNING":
                self.status = "FAILED"


class BlockingExecution(LifecycleExecution):
    def __init__(self) -> None:
        super().__init__()
        self.next_started = threading.Event()
        self.release_next = threading.Event()
        self.request_cancel.side_effect = self._cancel

    def _cancel(self, **_kwargs: object) -> bool:
        self.release_next.set()
        return True

    def execute(self, _prepared: PreparedTaskExecution):
        self.status = "RUNNING"
        try:
            yield TaskExecutionEvent(
                type="start",
                data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
            )
            self.provider_called = True
            self.next_started.set()
            self.release_next.wait(timeout=5)
            self.status = "COMPLETED"
            yield TaskExecutionEvent(
                type="done",
                data={"task_id": 7, "subtask_id": 11, "result": {"value": "ok"}},
            )
        finally:
            if self.status == "RUNNING":
                self.status = "FAILED"


class BlockingPrepareExecution(LifecycleExecution):
    def __init__(self) -> None:
        super().__init__()
        self.prepare_started = threading.Event()
        self.release_prepare = threading.Event()

    def prepare_send(self, **_kwargs: object) -> PreparedTaskExecution:
        self.prepare_started.set()
        self.release_prepare.wait(timeout=5)
        return _prepared()


class OwnershipExecution(LifecycleExecution):
    def __init__(
        self,
        prepared: PreparedTaskExecution,
        *,
        blocked: bool = False,
    ) -> None:
        super().__init__()
        self.prepared = prepared
        self.blocked = blocked
        self.prepare_started = threading.Event()
        self.release_prepare = threading.Event()

    def _prepare(self) -> PreparedTaskExecution:
        if self.blocked:
            # Models a repository commit followed by additional synchronous
            # preparation work that has not returned to the event loop yet.
            self.prepare_started.set()
            self.release_prepare.wait(timeout=5)
        return self.prepared

    def prepare_send(self, **_kwargs: object) -> PreparedTaskExecution:
        return self._prepare()

    def prepare_retry(self, **_kwargs: object) -> PreparedTaskExecution:
        return self._prepare()


class BlockingFirstAdvanceExecution(LifecycleExecution):
    def __init__(self) -> None:
        super().__init__()
        self.next_calls = 0
        self.next_started = threading.Event()
        self.release_next = threading.Event()

    def execute(self, _prepared: PreparedTaskExecution):
        self.next_calls += 1
        self.status = "RUNNING"
        self.next_started.set()
        self.release_next.wait(timeout=5)
        try:
            yield TaskExecutionEvent(
                type="start",
                data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
            )
            self.provider_called = True
        finally:
            if self.status == "RUNNING":
                self.status = "FAILED"


@run_async
async def test_task_join_returns_incremental_subtasks_and_active_stream(
    namespace: ChatNamespace,
) -> None:
    order: list[str] = []
    namespace.task_service.require_task_owner.side_effect = (
        lambda *_args, **_kwargs: order.append("verify")
    )
    namespace._enter_room.side_effect = (  # type: ignore[union-attr]
        lambda *_args, **_kwargs: order.append("enter")
    )
    namespace.task_service.list_subtasks_after.side_effect = (
        lambda *_args, **_kwargs: order.append("list") or [_subtask_dump()]
    )
    active = ActiveStreamSnapshot(
        task_id=7,
        subtask_id=12,
        generation_id="generation-12",
        offset=4,
        cached_content="part",
        blocks=(),
        started_at="2026-07-22T00:00:00Z",
        last_activity_at="2026-07-22T00:00:01Z",
        status_updated=None,
    )
    namespace.stream_store.get_active.side_effect = (
        lambda **_kwargs: order.append("active") or active
    )

    result = await namespace.on_task_join(
        "sid-1", {"task_id": 7, "after_message_id": 4}
    )

    assert result["subtasks"][0]["id"] == 11  # type: ignore[index]
    assert result["subtasks"][0]["message_id"] == 5  # type: ignore[index]
    assert result["streaming"]["cached_content"] == "part"  # type: ignore[index]
    assert result["streaming"]["generation_id"] == "generation-12"  # type: ignore[index]
    namespace.task_service.list_subtasks_after.assert_called_once_with(
        ANY,
        user_id=3,
        task_id=7,
        after_message_id=4,
    )
    namespace._enter_room.assert_awaited_once_with("sid-1", "task:7")  # type: ignore[union-attr]
    assert order == ["verify", "enter", "list", "active"]


@run_async
async def test_initial_task_join_requests_uncapped_history(
    namespace: ChatNamespace,
) -> None:
    namespace.task_service.list_subtasks_after.return_value = []
    namespace.stream_store.get_active.return_value = None

    await namespace.on_task_join("sid-1", {"task_id": 7, "after_message_id": 0})

    assert namespace.task_service.list_subtasks_after.call_args.kwargs[
        "after_message_id"
    ] is None


@run_async
async def test_non_owner_join_is_denied_without_entering_room(
    namespace: ChatNamespace,
) -> None:
    namespace.task_service.require_task_owner.side_effect = TaskServiceError(
        "task_not_found"
    )

    result = await namespace.on_task_join("sid-1", {"task_id": 99})

    assert result == {"error": {"code": "access_denied"}}
    namespace._enter_room.assert_not_awaited()  # type: ignore[union-attr]
    namespace.task_service.list_subtasks_after.assert_not_called()
    namespace.stream_store.get_active.assert_not_awaited()


@pytest.mark.parametrize("failure_stage", ["list", "active"])
@run_async
async def test_task_join_rolls_back_room_when_snapshot_fetch_fails(
    failure_stage: str,
    namespace: ChatNamespace,
) -> None:
    namespace.task_service.list_subtasks_after.return_value = []
    namespace.stream_store.get_active.return_value = None
    if failure_stage == "list":
        namespace.task_service.list_subtasks_after.side_effect = RuntimeError(
            "mysql unavailable"
        )
    else:
        namespace.stream_store.get_active.side_effect = RuntimeError(
            "redis unavailable"
        )

    result = await namespace.on_task_join("sid-1", {"task_id": 7})

    assert result == {"error": {"code": "request_failed"}}
    namespace._enter_room.assert_awaited_once_with("sid-1", "task:7")  # type: ignore[union-attr]
    namespace._leave_room.assert_awaited_once_with("sid-1", "task:7")  # type: ignore[union-attr]


@run_async
async def test_socket_validation_error_is_stable_and_does_not_log_private_payload(
    namespace: ChatNamespace,
    caplog: pytest.LogCaptureFixture,
) -> None:
    private_prompt = "private prompt must never be logged"
    private_context = "private selected-document text"
    private_token = "Bearer private-token"
    private_base64 = "cHJpdmF0ZS1iaW5hcnk="

    with caplog.at_level("WARNING", logger="app.api.ws.chat_namespace"):
        result = await namespace.on_chat_send(
            "sid-1",
            {
                "message": private_prompt,
                "context_ids": [1],
                "selected_document": private_context,
                "token": private_token,
                "binary": b"\xff\x00private-binary",
                "image_base64": private_base64,
            },
        )

    assert result == {"error": {"code": "invalid_payload"}}
    serialized = caplog.text
    for secret in (
        private_prompt,
        private_context,
        private_token,
        "private-binary",
        private_base64,
    ):
        assert secret not in serialized


@run_async
async def test_leave_and_cancel_use_lightweight_owner_check_only(
    namespace: ChatNamespace,
) -> None:
    namespace.stream_store.get_active.return_value = None
    namespace.execution.request_cancel.return_value = False

    leave = await namespace.on_task_leave("sid-1", {"task_id": 7})
    cancel = await namespace.on_chat_cancel("sid-1", {"task_id": 7})

    assert leave == {"task_id": 7}
    assert cancel == {"task_id": 7, "subtask_id": None, "accepted": False}
    assert namespace.task_service.require_task_owner.call_count == 2
    namespace.task_service.list_subtasks_after.assert_not_called()
    namespace.task_service.get_task.assert_not_called()


@run_async
async def test_prepare_and_brief_sync_work_crosses_to_thread(
    namespace: ChatNamespace,
    monkeypatch,
) -> None:
    prepared = _prepared()
    namespace.execution.prepare_send.return_value = prepared
    namespace.task_service.get_task_brief.return_value = _brief("PENDING")
    crossed: list[object] = []
    original = asyncio.to_thread

    async def record_to_thread(function, /, *args, **kwargs):
        crossed.append(function)
        return await original(function, *args, **kwargs)

    monkeypatch.setattr(chat_namespace_module.asyncio, "to_thread", record_to_thread)

    response = await namespace.on_chat_send(
        "sid-1", {"task_id": 7, "message": "hello", "context_ids": []}
    )
    await namespace._emit_task_state_best_effort(
        metadata=SimpleNamespace(user_id=3),  # type: ignore[arg-type]
        task_id=7,
    )

    assert response == {"task_id": 7, "subtask_id": 10, "message_id": 1}
    assert namespace.execution.prepare_send in crossed
    assert namespace._task_brief in crossed
    namespace.task_service.get_task_brief.assert_called_once()
    namespace.task_service.get_task.assert_not_called()
    namespace.task_service.list_subtasks_after.assert_not_called()


@run_async
async def test_chat_send_returns_durable_ids_and_only_schedules_execution(
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared()
    namespace.execution.prepare_send.return_value = prepared

    response = await namespace.on_chat_send(
        "sid-1", {"message": "hello", "context_ids": []}
    )

    assert response == {"task_id": 7, "subtask_id": 10, "message_id": 1}
    namespace._schedule_execution.assert_called_once_with(prepared)  # type: ignore[union-attr]
    namespace.emitter.emit_execution_event.assert_not_awaited()


@pytest.mark.parametrize("operation", ["send", "retry"])
@pytest.mark.parametrize("failure_stage", ["receipt", "room", "scheduler"])
@run_async
async def test_unscheduled_preparation_is_terminalized_on_every_normal_failure(
    operation: str,
    failure_stage: str,
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared(retry=operation == "retry")
    if failure_stage == "receipt":
        prepared = (
            replace(prepared, user_subtask_id=None, user_message_id=None)
            if operation == "send"
            else replace(prepared, assistant_subtask_id=12)
        )
    execution = OwnershipExecution(prepared)
    namespace._execution = execution  # type: ignore[assignment]
    if failure_stage == "room":
        namespace._enter_room.side_effect = RuntimeError("room unavailable")  # type: ignore[union-attr]
    if failure_stage == "scheduler":
        namespace._schedule_execution.side_effect = RuntimeError(  # type: ignore[union-attr]
            "scheduler unavailable"
        )

    if operation == "send":
        response = await namespace.on_chat_send(
            "sid-1", {"task_id": 7, "message": "hello", "context_ids": []}
        )
    else:
        response = await namespace.on_chat_retry(
            "sid-1", {"task_id": 7, "subtask_id": 11}
        )

    assert response == {"error": {"code": "request_failed"}}
    assert execution.status == "FAILED"
    assert execution.provider_called is False
    assert prepared.assistant_subtask_id not in namespace._execution_metadata


@pytest.mark.parametrize("operation", ["send", "retry"])
@run_async
async def test_cancelled_prepare_owns_and_terminalizes_committed_result(
    operation: str,
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared(retry=operation == "retry")
    execution = OwnershipExecution(prepared, blocked=True)
    namespace._execution = execution  # type: ignore[assignment]

    if operation == "send":
        handler = asyncio.create_task(
            namespace.on_chat_send(
                "sid-1", {"task_id": 7, "message": "hello", "context_ids": []}
            )
        )
    else:
        handler = asyncio.create_task(
            namespace.on_chat_retry(
                "sid-1", {"task_id": 7, "subtask_id": 11}
            )
        )
    assert await asyncio.to_thread(execution.prepare_started.wait, 2)
    handler.cancel()
    await asyncio.sleep(0)
    assert handler.done() is False
    execution.release_prepare.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handler, timeout=2)

    assert execution.status == "FAILED"
    assert execution.provider_called is False


@run_async
async def test_run_execution_yields_before_start_and_propagates_generation(
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared()
    namespace.execution.prepare_send.return_value = prepared
    await namespace.on_chat_send(
        "sid-1", {"task_id": 7, "message": "hello", "context_ids": []}
    )
    namespace.stream_store.start.return_value = "generation-11"
    namespace.stream_store.is_cancelled.return_value = False
    namespace.execution.execute.return_value = iter(
        [
            TaskExecutionEvent(
                type="start",
                data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
            )
        ]
    )
    pending = _brief("PENDING")
    running = _brief("RUNNING")
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=[pending, running]
    )

    task = asyncio.create_task(namespace.run_execution(prepared))
    assert namespace.stream_store.start.await_count == 0
    await asyncio.sleep(0)
    assert namespace.emitter.emit_execution_event.await_count == 0
    await task

    namespace.stream_store.start.assert_awaited_once_with(
        task_id=7, subtask_id=11
    )
    namespace.emitter.emit_execution_event.assert_awaited_once_with(
        ANY,
        generation_id="generation-11",
    )
    relevant = [
        item
        for item in namespace.emitter.mock_calls
        if item[0] in {"emit_task_status", "emit_execution_event"}
    ]
    assert relevant == [
        call.emit_task_status(user_id=3, task=pending),
        call.emit_execution_event(ANY, generation_id="generation-11"),
        call.emit_task_status(user_id=3, task=running),
    ]


@run_async
async def test_retry_emits_pending_status_before_retry_start(
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared(retry=True)
    namespace.execution.prepare_retry.return_value = prepared
    namespace.stream_store.start.return_value = "generation-retry"
    namespace.stream_store.is_cancelled.return_value = False
    start = TaskExecutionEvent(
        type="start",
        data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
    )
    namespace.execution.execute.return_value = iter([start])
    pending = _brief("PENDING")
    running = _brief("RUNNING")
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=[pending, running]
    )

    response = await namespace.on_chat_retry(
        "sid-1", {"task_id": 7, "subtask_id": 11}
    )
    assert response == {"task_id": 7, "subtask_id": 11}
    await namespace.run_execution(prepared)

    relevant = [
        item
        for item in namespace.emitter.mock_calls
        if item[0] in {"emit_task_status", "emit_execution_event"}
    ]
    assert relevant == [
        call.emit_task_status(user_id=3, task=pending),
        call.emit_execution_event(start, generation_id="generation-retry"),
        call.emit_task_status(user_id=3, task=running),
    ]


@run_async
async def test_new_task_emits_created_then_pending_before_start(
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared()
    namespace.execution.prepare_send.return_value = prepared
    namespace.stream_store.start.return_value = "generation-new"
    namespace.stream_store.is_cancelled.return_value = False
    start = TaskExecutionEvent(
        type="start",
        data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
    )
    namespace.execution.execute.return_value = iter([start])
    pending = _brief("PENDING")
    running = _brief("RUNNING")
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=[pending, running]
    )

    response = await namespace.on_chat_send(
        "sid-1", {"message": "new task", "context_ids": []}
    )
    assert response == {"task_id": 7, "subtask_id": 10, "message_id": 1}
    assert namespace._emitter.mock_calls == []
    await namespace.run_execution(prepared)

    relevant = [
        item
        for item in namespace.emitter.mock_calls
        if item[0]
        in {"emit_task_created", "emit_task_status", "emit_execution_event"}
    ]
    assert relevant == [
        call.emit_task_created(user_id=3, task=pending),
        call.emit_task_status(user_id=3, task=pending),
        call.emit_execution_event(start, generation_id="generation-new"),
        call.emit_task_status(user_id=3, task=running),
    ]


@run_async
async def test_stream_start_failure_primes_and_closes_durable_execution(
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared()
    execution = LifecycleExecution()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=lambda **_kwargs: _brief(execution.status)
    )
    namespace.stream_store.start.side_effect = RuntimeError("stream unavailable")

    with pytest.raises(RuntimeError, match="stream unavailable"):
        await namespace.run_execution(prepared)

    assert execution.status == "FAILED"
    assert execution.provider_called is False
    assert [
        item.kwargs["task"].status
        for item in namespace.emitter.emit_task_status.await_args_list
    ] == ["PENDING", "FAILED"]


@run_async
async def test_post_start_store_failure_primes_before_read_and_finalizes(
    namespace: ChatNamespace,
) -> None:
    execution = LifecycleExecution()
    store = MemoryChatStreamStore()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._stream_store = store
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=lambda **_kwargs: _brief(execution.status)
    )

    async def fail_cancel_read(**_kwargs: object) -> bool:
        raise RuntimeError("cancel store unavailable")

    store.is_cancelled = fail_cancel_read  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="cancel store unavailable"):
        await namespace.run_execution(_prepared())

    assert execution.status == "FAILED"
    assert execution.provider_called is False
    assert await store.get_active(task_id=7) is None
    assert [
        item.kwargs["task"].status
        for item in namespace.emitter.emit_task_status.await_args_list
    ] == ["PENDING", "RUNNING", "FAILED"]


@run_async
async def test_cancelled_first_advance_closes_without_advancing_twice(
    namespace: ChatNamespace,
) -> None:
    execution = BlockingFirstAdvanceExecution()
    store = MemoryChatStreamStore()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._stream_store = store
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=lambda **_kwargs: _brief(execution.status)
    )

    running = asyncio.create_task(namespace.run_execution(_prepared()))
    assert await asyncio.to_thread(execution.next_started.wait, 2)
    running.cancel()
    await asyncio.sleep(0)
    assert running.done() is False
    execution.release_next.set()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(running, timeout=2)

    assert execution.next_calls == 1
    assert execution.provider_called is False
    assert execution.status == "FAILED"
    assert await store.get_active(task_id=7) is None
    assert [
        item.kwargs["task"].status
        for item in namespace.emitter.emit_task_status.await_args_list
    ] == ["PENDING", "FAILED"]


@run_async
async def test_task_notification_failure_does_not_abort_execution(
    namespace: ChatNamespace,
) -> None:
    done = TaskExecutionEvent(
        type="done",
        data={"task_id": 7, "subtask_id": 11, "result": {"value": "ok"}},
    )
    execution = LifecycleExecution([done])
    namespace._execution = execution  # type: ignore[assignment]
    namespace._task_brief = Mock(return_value=_brief("PENDING"))  # type: ignore[method-assign]
    namespace.stream_store.start.return_value = "generation-11"
    namespace.stream_store.is_cancelled.return_value = False
    namespace.emitter.emit_task_status.side_effect = RuntimeError(
        "task notification unavailable"
    )

    await namespace.run_execution(_prepared())

    assert execution.status == "COMPLETED"
    assert execution.provider_called is True
    assert [
        item.args[0].type
        for item in namespace.emitter.emit_execution_event.await_args_list
    ] == ["start", "done"]


@run_async
async def test_abnormal_notification_failure_never_masks_stream_failure(
    namespace: ChatNamespace,
) -> None:
    execution = LifecycleExecution()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=lambda **_kwargs: _brief(execution.status)
    )
    namespace.stream_store.start.side_effect = RuntimeError("stream unavailable")
    namespace.emitter.emit_task_status.side_effect = RuntimeError(
        "task notification unavailable"
    )

    with pytest.raises(RuntimeError, match="stream unavailable"):
        await namespace.run_execution(_prepared())

    assert execution.status == "FAILED"
    assert execution.provider_called is False
    assert namespace.emitter.emit_task_status.await_count == 2


@pytest.mark.parametrize("event_type", ["chunk", "block_created"])
@run_async
async def test_nonterminal_stream_failure_persists_failed_and_clears_active(
    event_type: str,
    namespace: ChatNamespace,
) -> None:
    failure = TaskExecutionEvent(
        type=event_type,  # type: ignore[arg-type]
        data={"task_id": 7, "subtask_id": 11},
    )
    execution = LifecycleExecution([failure])
    store = MemoryChatStreamStore()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._stream_store = store
    namespace._task_brief = Mock(  # type: ignore[method-assign]
        side_effect=lambda **_kwargs: _brief(execution.status)
    )

    async def fail_on_nonterminal(
        event: TaskExecutionEvent,
        *,
        generation_id: str,
    ) -> None:
        del generation_id
        if event.type == event_type:
            raise RuntimeError("socket stream failed")

    namespace.emitter.emit_execution_event.side_effect = fail_on_nonterminal

    with pytest.raises(RuntimeError, match="socket stream failed"):
        await namespace.run_execution(_prepared())

    assert execution.status == "FAILED"
    assert await store.get_active(task_id=7) is None
    assert (
        namespace.emitter.emit_task_status.await_args_list[-1].kwargs[
            "task"
        ].status
        == "FAILED"
    )


@run_async
async def test_nonterminal_failure_never_deletes_replacement_generation(
    namespace: ChatNamespace,
) -> None:
    chunk = TaskExecutionEvent(
        type="chunk",
        data={"task_id": 7, "subtask_id": 11},
    )
    execution = LifecycleExecution([chunk])
    store = MemoryChatStreamStore()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._stream_store = store
    namespace._task_brief = Mock(return_value=_brief("PENDING"))  # type: ignore[method-assign]
    replacement: list[str] = []

    async def replace_then_fail(
        event: TaskExecutionEvent,
        *,
        generation_id: str,
    ) -> None:
        del generation_id
        if event.type == "chunk":
            replacement.append(await store.start(task_id=7, subtask_id=11))
            raise RuntimeError("socket stream failed")

    namespace.emitter.emit_execution_event.side_effect = replace_then_fail

    with pytest.raises(RuntimeError, match="socket stream failed"):
        await namespace.run_execution(_prepared())

    assert execution.status == "FAILED"
    active = await store.get_active(task_id=7)
    assert active is not None and active.generation_id == replacement[0]


@run_async
async def test_shutdown_cooperatively_drains_blocked_thread_advance(
    namespace: ChatNamespace,
) -> None:
    execution = BlockingExecution()
    namespace._execution = execution  # type: ignore[assignment]
    namespace._stream_store = MemoryChatStreamStore()
    namespace._task_brief = Mock(return_value=_brief("PENDING"))  # type: ignore[method-assign]
    namespace._schedule_execution = None
    prepared = _prepared()

    namespace.schedule_execution(prepared)
    assert await asyncio.to_thread(execution.next_started.wait, 2)
    tasks = list(namespace._background_tasks)

    await asyncio.wait_for(namespace.shutdown(warning_after=0.01), timeout=2)

    assert namespace._background_tasks == {}
    assert all(task.done() and not task.cancelled() for task in tasks)
    assert execution.status == "COMPLETED"
    execution.request_cancel.assert_called_with(user_id=3, task_id=7)


@run_async
async def test_shutdown_waits_for_blocked_prepare_then_fails_durable_turn(
    namespace: ChatNamespace,
) -> None:
    execution = BlockingPrepareExecution()
    namespace._execution = execution  # type: ignore[assignment]

    handler = asyncio.create_task(
        namespace.trigger_event(
            "chat:send",
            "sid-1",
            {"task_id": 7, "message": "hello", "context_ids": []},
        )
    )
    assert await asyncio.to_thread(execution.prepare_started.wait, 2)
    assert namespace._active_handlers == 1

    shutdown = asyncio.create_task(namespace.shutdown(warning_after=0.01))
    await asyncio.sleep(0)
    assert namespace._shutting_down is True
    assert shutdown.done() is False

    execution.release_prepare.set()
    response = await asyncio.wait_for(handler, timeout=2)
    await asyncio.wait_for(shutdown, timeout=2)

    assert response == {"error": {"code": "request_failed"}}
    assert execution.status == "FAILED"
    assert execution.provider_called is False
    assert namespace._active_handlers == 0


@run_async
async def test_namespace_startup_reopens_only_a_fully_drained_instance(
    namespace: ChatNamespace,
) -> None:
    namespace._shutting_down = True
    namespace._active_handlers = 1
    with pytest.raises(RuntimeError, match="socket_namespace_not_drained"):
        await namespace.startup()
    assert namespace._shutting_down is True

    namespace._active_handlers = 0
    await namespace.startup()
    assert namespace._shutting_down is False


@run_async
async def test_join_and_owner_workers_remain_inside_handler_barrier(
    namespace: ChatNamespace,
) -> None:
    namespace.stream_store.get_active.return_value = None
    cases = [
        (
            "task:join",
            ("sid-1", {"task_id": 7}),
            "_verify_task_owner",
            None,
        ),
        (
            "task:leave",
            ("sid-1", {"task_id": 7}),
            "_verify_task_owner",
            None,
        ),
    ]

    for event, args, attribute, return_value in cases:
        started = threading.Event()
        release = threading.Event()

        def blocking(*_args: object, **_kwargs: object) -> object:
            started.set()
            release.wait(timeout=5)
            return return_value

        setattr(namespace, attribute, blocking)
        handler = asyncio.create_task(namespace.trigger_event(event, *args))
        assert await asyncio.to_thread(started.wait, 2)
        assert namespace._active_handlers == 1
        handler.cancel()
        await asyncio.sleep(0)
        assert handler.done() is False
        assert namespace._active_handlers == 1
        release.set()
        await asyncio.gather(handler, return_exceptions=True)
        assert namespace._active_handlers == 0


@run_async
async def test_chat_cancel_sets_generation_flag_then_requests_execution(
    namespace: ChatNamespace,
) -> None:
    namespace.task_service.list_subtasks_after.return_value = []
    namespace.stream_store.get_active.return_value = ActiveStreamSnapshot(
        task_id=7,
        subtask_id=11,
        generation_id="generation-11",
        offset=0,
        cached_content="",
        blocks=(),
        started_at="2026-07-22T00:00:00Z",
        last_activity_at="2026-07-22T00:00:00Z",
        status_updated=None,
    )
    events: list[str] = []

    async def set_cancelled(**_kwargs) -> None:
        events.append("store")

    namespace.stream_store.set_cancelled.side_effect = set_cancelled
    namespace.execution.request_cancel.side_effect = lambda **_kwargs: (
        events.append("execution") or True
    )

    response = await namespace.on_chat_cancel("sid-1", {"task_id": 7})

    assert response == {"task_id": 7, "subtask_id": 11, "accepted": True}
    assert events == ["store", "execution"]
    namespace.stream_store.set_cancelled.assert_awaited_once_with(
        subtask_id=11,
        generation_id="generation-11",
    )


@run_async
async def test_chat_cancel_retries_replacement_and_always_requests_execution(
    namespace: ChatNamespace,
) -> None:
    namespace.task_service.list_subtasks_after.return_value = []
    old = ActiveStreamSnapshot(
        task_id=7,
        subtask_id=11,
        generation_id="generation-old",
        offset=0,
        cached_content="",
        blocks=(),
        started_at="2026-07-22T00:00:00Z",
        last_activity_at="2026-07-22T00:00:00Z",
        status_updated=None,
    )
    current = ActiveStreamSnapshot(
        task_id=7,
        subtask_id=12,
        generation_id="generation-current",
        offset=0,
        cached_content="",
        blocks=(),
        started_at="2026-07-22T00:00:01Z",
        last_activity_at="2026-07-22T00:00:01Z",
        status_updated=None,
    )
    namespace.stream_store.get_active.side_effect = [old, current]
    namespace.stream_store.set_cancelled.side_effect = [
        ChatStreamStaleGeneration(),
        None,
    ]
    namespace.execution.request_cancel.return_value = False

    response = await namespace.on_chat_cancel("sid-1", {"task_id": 7})

    assert response == {"task_id": 7, "subtask_id": 12, "accepted": True}
    assert namespace.stream_store.get_active.await_count == 2
    namespace.stream_store.set_cancelled.assert_has_awaits(
        [
            call(subtask_id=11, generation_id="generation-old"),
            call(subtask_id=12, generation_id="generation-current"),
        ]
    )
    namespace.execution.request_cancel.assert_called_once_with(user_id=3, task_id=7)


@run_async
async def test_chat_cancel_requests_execution_even_when_store_lookup_fails(
    namespace: ChatNamespace,
) -> None:
    namespace.task_service.list_subtasks_after.return_value = []
    namespace.stream_store.get_active.side_effect = RuntimeError("redis unavailable")
    namespace.execution.request_cancel.return_value = True

    response = await namespace.on_chat_cancel("sid-1", {"task_id": 7})

    assert response == {"error": {"code": "request_failed"}}
    namespace.execution.request_cancel.assert_called_once_with(user_id=3, task_id=7)


@run_async
async def test_chat_retry_reuses_failed_assistant_id(
    namespace: ChatNamespace,
) -> None:
    prepared = _prepared(retry=True)
    namespace.execution.prepare_retry.return_value = prepared

    response = await namespace.on_chat_retry(
        "sid-1", {"task_id": 7, "subtask_id": 11}
    )

    assert response == {"task_id": 7, "subtask_id": 11}
    namespace._schedule_execution.assert_called_once_with(prepared)  # type: ignore[union-attr]


@run_async
async def test_connect_rejects_missing_and_invalid_tokens(
    namespace: ChatNamespace,
) -> None:
    with pytest.raises(socketio.exceptions.ConnectionRefusedError) as missing:
        await namespace.on_connect("sid", {}, None)
    assert missing.value.error_args["data"] == {"code": "auth_required"}
    with pytest.raises(socketio.exceptions.ConnectionRefusedError) as invalid:
        await namespace.on_connect("sid", {}, {"token": "private.invalid.token"})
    assert invalid.value.error_args["data"] == {"code": "invalid_token"}


@run_async
async def test_connect_validates_current_user_and_enters_private_room(
    namespace: ChatNamespace,
) -> None:
    with session_scope(namespace.session_factory) as session:
        user = models.User(
            username="alice",
            password_hash="unused",
            display_name="Alice",
            token_version=4,
            is_active=True,
        )
        session.add(user)
        session.flush()
        user_id = user.id
    token = create_access_token("alice", user_id, 4)
    namespace.save_session = AsyncMock()  # type: ignore[method-assign]

    await namespace.on_connect("sid", {}, {"token": token})

    namespace.save_session.assert_awaited_once_with("sid", {"user_id": user_id})
    namespace._enter_room.assert_awaited_once_with(  # type: ignore[union-attr]
        "sid", f"user:{user_id}"
    )

    revoked = create_access_token("alice", user_id, 3)
    with pytest.raises(socketio.exceptions.ConnectionRefusedError):
        await namespace.on_connect("revoked", {}, {"token": revoked})


@pytest.mark.parametrize("failure_stage", ["auth", "room", "session"])
@run_async
async def test_connect_operational_failures_reject_without_partial_state(
    failure_stage: str,
    namespace: ChatNamespace,
) -> None:
    server = socketio.AsyncServer(async_mode="asgi")
    server.register_namespace(namespace)
    server._send_packet = AsyncMock()  # type: ignore[method-assign]
    socket = SimpleNamespace(session={}, closed=False)
    server.eio.sockets["eio-1"] = socket  # type: ignore[assignment]
    server.environ["eio-1"] = {}
    token = create_access_token("alice", 3, 1)
    room_seen_before_save = False

    if failure_stage == "auth":
        namespace._token_user_is_current = Mock(  # type: ignore[method-assign]
            side_effect=RuntimeError("auth database unavailable")
        )
    else:
        namespace._token_user_is_current = Mock(  # type: ignore[method-assign]
            return_value=True
        )

    if failure_stage == "room":
        async def fail_after_partial_room(sid: str, room: str) -> None:
            await namespace.enter_room(sid, room)
            raise RuntimeError("room unavailable")

        namespace._enter_room = fail_after_partial_room
    elif failure_stage == "session":
        namespace._enter_room = None

        async def fail_after_partial_session(
            sid: str,
            session: dict[str, int],
        ) -> None:
            nonlocal room_seen_before_save
            room = server.manager.rooms["/chat"]["user:3"]
            room_seen_before_save = sid in room
            socket.session["/chat"] = dict(session)
            server.environ["eio-1"]["/chat"] = dict(session)
            raise RuntimeError("session unavailable")

        namespace.save_session = fail_after_partial_session  # type: ignore[method-assign]

    await server._handle_connect(  # type: ignore[attr-defined]
        "eio-1",
        "/chat",
        {"token": token},
    )

    if failure_stage == "session":
        assert room_seen_before_save is True
    assert not server.manager.rooms.get("/chat")
    assert "/chat" not in socket.session
    assert "/chat" not in server.environ["eio-1"]
    sent = server._send_packet.await_args_list  # type: ignore[union-attr]
    assert len(sent) == 1
    assert sent[0].args[1].packet_type == socketio.packet.CONNECT_ERROR
    assert sent[0].args[1].data["data"]["code"] == "auth_unavailable"


@pytest.mark.parametrize("revoked", [False, True])
@run_async
async def test_real_connect_preserves_invalid_token_rejection_codes(
    revoked: bool,
    namespace: ChatNamespace,
) -> None:
    server = socketio.AsyncServer(async_mode="asgi")
    server.register_namespace(namespace)
    server._send_packet = AsyncMock()  # type: ignore[method-assign]
    server.environ["eio-1"] = {}
    namespace._token_user_is_current = Mock(  # type: ignore[method-assign]
        return_value=False
    )
    token = (
        create_access_token("alice", 3, 1)
        if revoked
        else "private.invalid.token"
    )

    await server._handle_connect(  # type: ignore[attr-defined]
        "eio-1",
        "/chat",
        {"token": token},
    )

    assert not server.manager.rooms.get("/chat")
    sent = server._send_packet.await_args_list  # type: ignore[union-attr]
    assert len(sent) == 1
    assert sent[0].args[1].data["data"]["code"] == "invalid_token"


@run_async
async def test_cancelled_connect_auth_rejects_and_removes_provisional_sid(
    namespace: ChatNamespace,
) -> None:
    server = socketio.AsyncServer(async_mode="asgi")
    server.register_namespace(namespace)
    server._send_packet = AsyncMock()  # type: ignore[method-assign]
    socket = SimpleNamespace(session={}, closed=False)
    server.eio.sockets["eio-1"] = socket  # type: ignore[assignment]
    server.environ["eio-1"] = {}
    token = create_access_token("alice", 3, 1)
    started = threading.Event()
    release = threading.Event()

    def blocked_auth(*_args: object) -> bool:
        started.set()
        release.wait(timeout=5)
        return True

    namespace._token_user_is_current = blocked_auth  # type: ignore[method-assign]
    namespace.save_session = AsyncMock()  # type: ignore[method-assign]

    connecting = asyncio.create_task(
        server._handle_connect(  # type: ignore[attr-defined]
            "eio-1",
            "/chat",
            {"token": token},
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    assert namespace._active_handlers == 1
    connecting.cancel()
    await asyncio.sleep(0)
    assert connecting.done() is False
    release.set()
    await asyncio.wait_for(connecting, timeout=2)

    namespace.save_session.assert_not_awaited()
    assert namespace._active_handlers == 0
    assert not server.manager.rooms.get("/chat")
    assert "/chat" not in socket.session
    assert "/chat" not in server.environ["eio-1"]
    sent = server._send_packet.await_args_list  # type: ignore[union-attr]
    assert len(sent) == 1
    assert sent[0].args[1].packet_type == socketio.packet.CONNECT_ERROR
    assert sent[0].args[1].data["data"]["code"] == "auth_unavailable"


@run_async
async def test_emitter_writes_generation_fenced_state_before_events() -> None:
    sio = AsyncMock()
    store = AsyncMock()
    emitter = WebSocketChatEmitter(sio=sio, stream_store=store)
    created = create_text_block(block_id="text-1")
    updated = append_text_block(created, "😀", status="done")

    await emitter.emit_execution_event(
        TaskExecutionEvent(
            type="block_created",
            data={"task_id": 7, "subtask_id": 11, "block": created},
        ),
        generation_id="generation-11",
    )
    await emitter.emit_execution_event(
        TaskExecutionEvent(
            type="chunk",
            data={
                "task_id": 7,
                "subtask_id": 11,
                "block_id": "text-1",
                "block_offset": 0,
                "offset": 0,
                "content": "😀",
            },
        ),
        generation_id="generation-11",
    )
    await emitter.emit_execution_event(
        TaskExecutionEvent(
            type="block_updated",
            data={"task_id": 7, "subtask_id": 11, "block": updated},
        ),
        generation_id="generation-11",
    )
    await emitter.emit_status_updated(
        task_id=7,
        subtask_id=11,
        generation_id="generation-11",
        status={"remaining_input_tokens": 100},
    )
    await emitter.emit_execution_event(
        TaskExecutionEvent(
            type="done",
            data={"task_id": 7, "subtask_id": 11, "result": {"value": "😀"}},
        ),
        generation_id="generation-11",
    )

    store.upsert_block.assert_has_awaits(
        [
            call(
                subtask_id=11,
                generation_id="generation-11",
                block=dict(created),
            ),
            call(
                subtask_id=11,
                generation_id="generation-11",
                block=dict(updated),
            ),
        ]
    )
    store.append_text.assert_awaited_once_with(
        subtask_id=11,
        generation_id="generation-11",
        block_id="text-1",
        offset=0,
        content="😀",
    )
    store.set_status_snapshot.assert_awaited_once_with(
        subtask_id=11,
        generation_id="generation-11",
        payload={"remaining_input_tokens": 100},
    )
    store.finalize.assert_awaited_once_with(
        task_id=7,
        subtask_id=11,
        generation_id="generation-11",
    )
    assert sio.emit.await_args_list[-1].args[0] == "chat:done"


@run_async
async def test_stale_generation_never_emits_a_chunk() -> None:
    sio = AsyncMock()
    store = AsyncMock()
    store.append_text.side_effect = ChatStreamStaleGeneration()
    emitter = WebSocketChatEmitter(sio=sio, stream_store=store)

    with pytest.raises(ChatStreamStaleGeneration):
        await emitter.emit_execution_event(
            TaskExecutionEvent(
                type="chunk",
                data={
                    "task_id": 7,
                    "subtask_id": 11,
                    "block_id": "text-1",
                    "block_offset": 0,
                    "offset": 0,
                    "content": "secret",
                },
            ),
            generation_id="stale-generation",
        )

    sio.emit.assert_not_awaited()


@run_async
async def test_stale_start_generation_never_emits_or_replaces_current_owner() -> None:
    sio = AsyncMock()
    store = MemoryChatStreamStore()
    emitter = WebSocketChatEmitter(sio=sio, stream_store=store)
    stale = await store.start(task_id=7, subtask_id=11)
    current = await store.start(task_id=7, subtask_id=11)

    with pytest.raises(ChatStreamStaleGeneration):
        await emitter.emit_execution_event(
            TaskExecutionEvent(
                type="start",
                data={"task_id": 7, "subtask_id": 11, "status": "RUNNING"},
            ),
            generation_id=stale,
        )

    sio.emit.assert_not_awaited()
    active = await store.get_active(task_id=7)
    assert active is not None and active.generation_id == current


@pytest.mark.parametrize("terminal", ["done", "error", "cancelled"])
@run_async
async def test_every_stale_terminal_is_suppressed_without_deleting_replacement(
    terminal: str,
) -> None:
    sio = AsyncMock()
    store = MemoryChatStreamStore()
    emitter = WebSocketChatEmitter(sio=sio, stream_store=store)
    stale = await store.start(task_id=7, subtask_id=11)
    current = await store.start(task_id=7, subtask_id=11)
    data: dict[str, object] = {
        "task_id": 7,
        "subtask_id": 11,
        "result": {"value": "old"},
    }
    if terminal == "error":
        data["code"] = "provider_call_failed"

    with pytest.raises(ChatStreamStaleGeneration):
        await emitter.emit_execution_event(
            TaskExecutionEvent(type=terminal, data=data),  # type: ignore[arg-type]
            generation_id=stale,
        )

    sio.emit.assert_not_awaited()
    active = await store.get_active(task_id=7)
    assert active is not None and active.generation_id == current


@pytest.mark.parametrize("status", ["PENDING", "FAILED"])
@run_async
async def test_task_status_is_broadcast_to_task_and_user_rooms(
    status: str,
) -> None:
    sio = AsyncMock()
    emitter = WebSocketChatEmitter(sio=sio, stream_store=AsyncMock())
    task = _brief(status)

    await emitter.emit_task_status(user_id=3, task=task)

    assert [item.kwargs["room"] for item in sio.emit.await_args_list] == [
        "task:7",
        "user:3",
    ]
    assert all(
        item.args[1]["task"]["status"] == status
        for item in sio.emit.await_args_list
    )


@run_async
async def test_terminal_transport_failure_still_finalizes_active_generation() -> None:
    sio = AsyncMock()
    sio.emit.side_effect = RuntimeError("transport unavailable")
    store = AsyncMock()
    emitter = WebSocketChatEmitter(sio=sio, stream_store=store)

    with pytest.raises(RuntimeError, match="transport unavailable"):
        await emitter.emit_execution_event(
            TaskExecutionEvent(
                type="done",
                data={"task_id": 7, "subtask_id": 11, "result": {"value": "ok"}},
            ),
            generation_id="generation-11",
        )

    store.finalize.assert_awaited_once_with(
        task_id=7,
        subtask_id=11,
        generation_id="generation-11",
    )
