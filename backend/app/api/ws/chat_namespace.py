from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from dataclasses import dataclass
import logging
from typing import Any, cast

from fastapi import FastAPI, HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
import socketio

from app.core.auth import TokenInvalidError, decode_access_token
from app.db import models
from app.db.session import session_scope
from app.schemas.socket_events import (
    ActiveStreamSnapshotPayload,
    ChatCancelAck,
    ChatCancelPayload,
    ChatRetryAck,
    ChatRetryPayload,
    ChatSendAck,
    ChatSendPayload,
    SocketErrorAck,
    SocketErrorDetail,
    TaskBriefPayload,
    TaskJoinAck,
    TaskJoinPayload,
    TaskLeaveAck,
    TaskLeavePayload,
)
from app.services.chat_stream_store import (
    ChatStreamNotActive,
    ChatStreamStaleGeneration,
    ChatStreamStore,
)
from app.services.task_execution_service import (
    PreparedTaskExecution,
    TaskExecutionEvent,
    TaskExecutionService,
)
from app.services.task_service import TaskService, TaskServiceError


logger = logging.getLogger(__name__)
_ITERATOR_END = object()


@dataclass(frozen=True, slots=True)
class _ExecutionMetadata:
    user_id: int
    task_created: bool


class ChatNamespace(socketio.AsyncNamespace):
    def __init__(
        self,
        *,
        app: FastAPI | None = None,
        task_service: TaskService | None = None,
        execution: TaskExecutionService | None = None,
        stream_store: ChatStreamStore | None = None,
        emitter: Any | None = None,
        session_factory: sessionmaker[Session] | None = None,
        enter_room: Callable[[str, str], Any] | None = None,
        leave_room: Callable[[str, str], Any] | None = None,
        schedule_execution: Callable[[PreparedTaskExecution], None] | None = None,
    ) -> None:
        super().__init__(namespace="/chat")
        self.app = app
        self._task_service = task_service
        self._execution = execution
        self._stream_store = stream_store
        self._emitter = emitter
        self._session_factory = session_factory
        self._enter_room = enter_room
        self._leave_room = leave_room
        self._schedule_execution = schedule_execution
        self._execution_metadata: dict[int, _ExecutionMetadata] = {}
        self._background_tasks: dict[
            asyncio.Task[None], PreparedTaskExecution
        ] = {}
        self._shutting_down = False
        self._active_handlers = 0
        self._handlers_drained = asyncio.Event()
        self._handlers_drained.set()

    async def trigger_event(self, event: str, *args: object) -> object:
        if event == "disconnect":
            # Socket.IO must always be able to release a disconnected SID,
            # including while application shutdown rejects new work.
            return await super().trigger_event(event, *args)
        if not self._begin_handler():
            if event == "connect":
                raise _connection_error("server_shutting_down")
            return _error_ack(RuntimeError("socket_shutting_down"))
        handlers = {
            "connect": self.on_connect,
            "task:join": self.on_task_join,
            "task:leave": self.on_task_leave,
            "chat:send": self.on_chat_send,
            "chat:cancel": self.on_chat_cancel,
            "chat:retry": self.on_chat_retry,
        }
        try:
            handler = handlers.get(event)
            if handler is not None:
                return await handler(*args)
            return await super().trigger_event(event, *args)
        finally:
            self._end_handler()

    async def on_connect(
        self,
        sid: str,
        _environ: dict[str, object],
        auth: object,
    ) -> None:
        token = auth.get("token") if isinstance(auth, dict) else None
        if not isinstance(token, str) or not token:
            raise _connection_error("auth_required")
        try:
            payload = decode_access_token(token)
        except TokenInvalidError:
            raise _connection_error("invalid_token") from None
        try:
            authenticated = await _run_thread_shielded(
                self._token_user_is_current,
                payload.user_id,
                payload.username,
                payload.token_version,
            )
        except asyncio.CancelledError:
            self._clear_connect_session(sid)
            raise _connection_error("auth_unavailable") from None
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as error:
            self._clear_connect_session(sid)
            _safe_log("socket_connect_auth_unavailable", error)
            raise _connection_error("auth_unavailable") from None
        if not authenticated:
            raise _connection_error("invalid_token")
        try:
            # Room membership is server-controlled and is removed by
            # AsyncServer's refusal path. Save the application session last so
            # no later connection step can fail after it is committed.
            await self._enter(sid, _user_room(payload.user_id))
            await self.save_session(sid, {"user_id": payload.user_id})
        except asyncio.CancelledError:
            self._clear_connect_session(sid)
            raise _connection_error("auth_unavailable") from None
        except (SystemExit, KeyboardInterrupt):
            raise
        except Exception as error:
            self._clear_connect_session(sid)
            _safe_log("socket_connect_setup_unavailable", error)
            raise _connection_error("auth_unavailable") from None

    async def on_task_join(self, sid: str, data: object) -> dict[str, object]:
        try:
            payload = TaskJoinPayload.model_validate(data)
            user_id = await self._user_id(sid)
            await _run_thread_shielded(
                self._verify_task_owner,
                user_id=user_id,
                task_id=payload.task_id,
            )
            await self._enter(sid, _task_room(payload.task_id))
            try:
                subtasks = await _run_thread_shielded(
                    self._list_subtasks_after,
                    user_id,
                    payload.task_id,
                    (
                        None
                        if payload.after_message_id in {None, 0}
                        else payload.after_message_id
                    ),
                )
                active = await self.stream_store.get_active(task_id=payload.task_id)
                streaming = (
                    None
                    if active is None
                    else ActiveStreamSnapshotPayload(
                        task_id=active.task_id,
                        subtask_id=active.subtask_id,
                        generation_id=active.generation_id,
                        offset=active.offset,
                        cached_content=active.cached_content,
                        blocks=list(active.blocks),
                        started_at=active.started_at,
                        last_activity_at=active.last_activity_at,
                        status_updated=active.status_updated,
                    )
                )
                return TaskJoinAck(
                    task_id=payload.task_id,
                    subtasks=subtasks,
                    streaming=streaming,
                ).model_dump(mode="json")
            except BaseException:
                try:
                    await self._leave(sid, _task_room(payload.task_id))
                except (SystemExit, KeyboardInterrupt):
                    raise
                except Exception as cleanup_error:
                    _safe_log("socket_task_join_room_rollback_failed", cleanup_error)
                raise
        except Exception as error:
            return _error_ack(error)

    async def on_task_leave(self, sid: str, data: object) -> dict[str, object]:
        try:
            payload = TaskLeavePayload.model_validate(data)
            user_id = await self._user_id(sid)
            await _run_thread_shielded(
                self._verify_task_owner,
                user_id=user_id,
                task_id=payload.task_id,
            )
            await self._leave(sid, _task_room(payload.task_id))
            return TaskLeaveAck(task_id=payload.task_id).model_dump(mode="json")
        except Exception as error:
            return _error_ack(error)

    async def on_chat_send(self, sid: str, data: object) -> dict[str, object]:
        prepared: PreparedTaskExecution | None = None
        scheduled = False
        try:
            payload = ChatSendPayload.model_validate(data)
            user_id = await self._user_id(sid)
            if self._shutting_down:
                raise RuntimeError("socket_shutting_down")
            prepared = await self._prepare_owned(
                self.execution.prepare_send,
                user_id=user_id,
                request=payload.to_request(),
            )
            if prepared.user_subtask_id is None or prepared.user_message_id is None:
                raise RuntimeError("socket_send_receipt_invalid")
            await self._enter(sid, _task_room(prepared.task_id))
            if self._shutting_down:
                raise RuntimeError("socket_shutting_down")
            self._execution_metadata[prepared.assistant_subtask_id] = (
                _ExecutionMetadata(
                    user_id=user_id,
                    task_created=payload.task_id is None,
                )
            )
            self.schedule_execution(prepared)
            scheduled = True
            return ChatSendAck(
                task_id=prepared.task_id,
                subtask_id=prepared.user_subtask_id,
                message_id=prepared.user_message_id,
            ).model_dump(mode="json")
        except asyncio.CancelledError:
            if prepared is not None and not scheduled:
                await self._terminalize_cancelled_preparation(prepared)
            raise
        except Exception as error:
            if prepared is not None and not scheduled:
                await self._terminalize_unscheduled(prepared)
            return _error_ack(error)

    async def on_chat_cancel(self, sid: str, data: object) -> dict[str, object]:
        try:
            payload = ChatCancelPayload.model_validate(data)
            user_id = await self._user_id(sid)
            await _run_thread_shielded(
                self._verify_task_owner,
                user_id=user_id,
                task_id=payload.task_id,
            )
            active = None
            redis_cancelled = False
            execution_cancelled = False
            try:
                for _attempt in range(2):
                    active = await self.stream_store.get_active(
                        task_id=payload.task_id
                    )
                    if active is None:
                        break
                    try:
                        await self.stream_store.set_cancelled(
                            subtask_id=active.subtask_id,
                            generation_id=active.generation_id,
                        )
                    except (ChatStreamStaleGeneration, ChatStreamNotActive):
                        active = None
                        continue
                    redis_cancelled = True
                    break
            finally:
                execution_cancelled = await _run_thread_shielded(
                    self.execution.request_cancel,
                    user_id=user_id,
                    task_id=payload.task_id,
                )
            return ChatCancelAck(
                task_id=payload.task_id,
                subtask_id=active.subtask_id if redis_cancelled else None,
                accepted=execution_cancelled or redis_cancelled,
            ).model_dump(mode="json")
        except Exception as error:
            return _error_ack(error)

    async def on_chat_retry(self, sid: str, data: object) -> dict[str, object]:
        prepared: PreparedTaskExecution | None = None
        scheduled = False
        try:
            payload = ChatRetryPayload.model_validate(data)
            user_id = await self._user_id(sid)
            if self._shutting_down:
                raise RuntimeError("socket_shutting_down")
            prepared = await self._prepare_owned(
                self.execution.prepare_retry,
                user_id=user_id,
                task_id=payload.task_id,
                subtask_id=payload.subtask_id,
            )
            if prepared.assistant_subtask_id != payload.subtask_id:
                raise RuntimeError("socket_retry_receipt_invalid")
            await self._enter(sid, _task_room(prepared.task_id))
            if self._shutting_down:
                raise RuntimeError("socket_shutting_down")
            self._execution_metadata[prepared.assistant_subtask_id] = (
                _ExecutionMetadata(user_id=user_id, task_created=False)
            )
            self.schedule_execution(prepared)
            scheduled = True
            return ChatRetryAck(
                task_id=prepared.task_id,
                subtask_id=prepared.assistant_subtask_id,
            ).model_dump(mode="json")
        except asyncio.CancelledError:
            if prepared is not None and not scheduled:
                await self._terminalize_cancelled_preparation(prepared)
            raise
        except Exception as error:
            if prepared is not None and not scheduled:
                await self._terminalize_unscheduled(prepared)
            return _error_ack(error)

    def schedule_execution(self, prepared: PreparedTaskExecution) -> None:
        if self._schedule_execution is not None:
            self._schedule_execution(prepared)
            return
        task = asyncio.create_task(self.run_execution(prepared))
        self._background_tasks[task] = prepared
        task.add_done_callback(self._execution_done)

    async def startup(self) -> None:
        """Open the namespace for a new lifespan after a complete drain."""
        if (
            self._active_handlers
            or self._background_tasks
            or self._execution_metadata
        ):
            raise RuntimeError("socket_namespace_not_drained")
        self._shutting_down = False

    async def run_execution(self, prepared: PreparedTaskExecution) -> None:
        # The handler must return the durable User Subtask ACK before any
        # server-side stream event can be observed.
        await asyncio.sleep(0)
        metadata = self._execution_metadata.pop(
            prepared.assistant_subtask_id,
            _ExecutionMetadata(
                user_id=prepared.runtime_turn.context.user_id,
                task_created=False,
            ),
        )
        iterator = self.execution.execute(prepared)
        generation_id: str | None = None
        iterator_primed = False
        try:
            await self._emit_task_state_best_effort(
                metadata=metadata,
                task_id=prepared.task_id,
                created=metadata.task_created,
            )
            generation_id = await self.stream_store.start(
                task_id=prepared.task_id,
                subtask_id=prepared.assistant_subtask_id,
            )
            while True:
                iterator_primed = True
                item = await _advance_iterator(iterator)
                if item is _ITERATOR_END:
                    break
                event = cast(TaskExecutionEvent, item)
                await self.emitter.emit_execution_event(
                    event,
                    generation_id=generation_id,
                )
                if event.type == "start" or event.type in {
                    "done",
                    "error",
                    "cancelled",
                }:
                    await self._emit_task_state_best_effort(
                        metadata=metadata,
                        task_id=prepared.task_id,
                    )
                if event.type in {"done", "error", "cancelled"}:
                    break
                if await self.stream_store.is_cancelled(
                    subtask_id=prepared.assistant_subtask_id,
                    generation_id=generation_id,
                ):
                    await _run_thread_shielded(
                        self.execution.request_cancel,
                        user_id=metadata.user_id,
                        task_id=prepared.task_id,
                    )
        except (ChatStreamStaleGeneration, ChatStreamNotActive):
            # A retry/replacement owns the active slot now. Closing this old
            # iterator invokes TaskExecutionService's fail-safe cleanup without
            # letting stale events overwrite the new generation.
            await _close_execution_iterator(
                iterator,
                primed=iterator_primed,
            )
            await self._emit_task_state_best_effort(
                metadata=metadata,
                task_id=prepared.task_id,
            )
            if generation_id is not None:
                await self._cleanup_stream_best_effort(
                    prepared=prepared,
                    generation_id=generation_id,
                )
            return
        except BaseException:
            await _close_execution_iterator(
                iterator,
                primed=iterator_primed,
            )
            await self._emit_task_state_best_effort(
                metadata=metadata,
                task_id=prepared.task_id,
            )
            if generation_id is not None:
                await self._cleanup_stream_best_effort(
                    prepared=prepared,
                    generation_id=generation_id,
                )
            raise
        else:
            await _close_iterator_async(iterator)

    async def shutdown(self, *, warning_after: float = 5.0) -> None:
        """Gate handlers, then fully drain work; the threshold only warns."""
        self._shutting_down = True
        await self._handlers_drained.wait()
        first_wait = True
        while self._background_tasks:
            entries = list(self._background_tasks.items())
            for _task, prepared in entries:
                try:
                    await _run_thread_shielded(
                        self.execution.request_cancel,
                        user_id=prepared.runtime_turn.context.user_id,
                        task_id=prepared.task_id,
                    )
                except Exception as error:
                    _safe_log(
                        "socket_execution_shutdown_cancel_failed",
                        error,
                    )
            tasks = {task for task, _prepared in entries}
            if first_wait:
                _done, pending = await asyncio.wait(
                    tasks,
                    timeout=warning_after,
                )
                first_wait = False
                if pending:
                    logger.warning(
                        "socket_execution_shutdown_waiting",
                        extra={"pending_count": len(pending)},
                    )
                    await asyncio.gather(*pending, return_exceptions=True)
            else:
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(0)

    def _execution_done(self, task: asyncio.Task[None]) -> None:
        self._background_tasks.pop(task, None)
        _log_background_failure(task)

    async def _emit_task_state_best_effort(
        self,
        *,
        metadata: _ExecutionMetadata,
        task_id: int,
        created: bool = False,
    ) -> None:
        try:
            task = await _run_thread_shielded(
                self._task_brief,
                user_id=metadata.user_id,
                task_id=task_id,
            )
            if created:
                await self.emitter.emit_task_created(
                    user_id=metadata.user_id,
                    task=task,
                )
            await self.emitter.emit_task_status(
                user_id=metadata.user_id,
                task=task,
            )
        except Exception as error:
            _safe_log("socket_task_notification_failed", error)

    async def _cleanup_stream_best_effort(
        self,
        *,
        prepared: PreparedTaskExecution,
        generation_id: str,
    ) -> None:
        try:
            await self.stream_store.validate_generation(
                task_id=prepared.task_id,
                subtask_id=prepared.assistant_subtask_id,
                generation_id=generation_id,
            )
            await self.stream_store.finalize(
                task_id=prepared.task_id,
                subtask_id=prepared.assistant_subtask_id,
                generation_id=generation_id,
            )
        except (ChatStreamStaleGeneration, ChatStreamNotActive):
            return
        except Exception as error:
            _safe_log("socket_stream_cleanup_failed", error)

    async def _fail_prepared_execution(
        self,
        prepared: PreparedTaskExecution,
    ) -> None:
        iterator = self.execution.execute(prepared)
        await _prime_and_close_iterator(iterator)

    async def _prepare_owned(
        self,
        function: Callable[..., PreparedTaskExecution],
        **kwargs: object,
    ) -> PreparedTaskExecution:
        work = asyncio.create_task(asyncio.to_thread(function, **kwargs))
        cancellation: asyncio.CancelledError | None = None
        while not work.done():
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError as error:
                if cancellation is None:
                    cancellation = error
            except BaseException:
                break
        try:
            prepared = work.result()
        except BaseException as error:
            if cancellation is None:
                raise
            _safe_log("socket_prepare_after_cancel_failed", error)
            raise cancellation from None
        if cancellation is not None:
            await self._terminalize_cancelled_preparation(prepared)
            raise cancellation
        return prepared

    async def _terminalize_unscheduled(
        self,
        prepared: PreparedTaskExecution,
    ) -> None:
        self._execution_metadata.pop(prepared.assistant_subtask_id, None)
        try:
            await self._fail_prepared_execution(prepared)
        except BaseException as error:
            _safe_log("socket_unscheduled_terminalization_failed", error)

    async def _terminalize_cancelled_preparation(
        self,
        prepared: PreparedTaskExecution,
    ) -> None:
        try:
            await self._terminalize_unscheduled(prepared)
        except BaseException as error:
            _safe_log("socket_cancelled_terminalization_failed", error)

    def _begin_handler(self) -> bool:
        # This check/increment has no await and is therefore atomic on the
        # namespace's event loop with shutdown's gate transition.
        if self._shutting_down:
            return False
        self._active_handlers += 1
        self._handlers_drained.clear()
        return True

    def _end_handler(self) -> None:
        self._active_handlers -= 1
        if self._active_handlers == 0:
            self._handlers_drained.set()

    def _verify_task_owner(self, *, user_id: int, task_id: int) -> None:
        with session_scope(self.session_factory) as session:
            self.task_service.require_task_owner(
                session,
                user_id=user_id,
                task_id=task_id,
            )

    def _task_brief(self, *, user_id: int, task_id: int) -> TaskBriefPayload:
        with session_scope(self.session_factory) as session:
            task = self.task_service.get_task_brief(
                session,
                user_id=user_id,
                task_id=task_id,
            )
        return TaskBriefPayload(
            id=task.id,
            name=task.name,
            href=task.href,
            status=task.status,
            agent=task.agent.model_dump(mode="json"),
            model_override=task.model_override,
            created_at=task.created_at,
            updated_at=task.updated_at,
        )

    def _list_subtasks_after(
        self,
        user_id: int,
        task_id: int,
        after_message_id: int | None,
    ) -> list[object]:
        with session_scope(self.session_factory) as session:
            return list(
                self.task_service.list_subtasks_after(
                    session,
                    user_id=user_id,
                    task_id=task_id,
                    after_message_id=after_message_id,
                )
            )

    def _token_user_is_current(
        self,
        user_id: int,
        username: str,
        token_version: int,
    ) -> bool:
        with session_scope(self.session_factory) as session:
            user = session.scalar(select(models.User).where(models.User.id == user_id))
            return bool(
                user is not None
                and user.is_active
                and user.username == username
                and user.token_version == token_version
            )

    def _clear_connect_session(self, sid: str) -> None:
        server = getattr(self, "server", None)
        if server is None:
            return
        try:
            eio_sid = server.manager.eio_sid_from_sid(sid, self.namespace)
            socket = server.eio.sockets.get(eio_sid)
            session = getattr(socket, "session", None)
            if isinstance(session, dict):
                session.pop(self.namespace, None)
            environ = server.environ.get(eio_sid)
            if isinstance(environ, dict):
                environ.pop(self.namespace, None)
        except Exception as error:
            _safe_log("socket_connect_session_cleanup_failed", error)

    async def _user_id(self, sid: str) -> int:
        socket_session = await self.get_session(sid)
        user_id = socket_session.get("user_id")
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            raise PermissionError("auth_required")
        return user_id

    async def _enter(self, sid: str, room: str) -> None:
        result = (
            self._enter_room(sid, room)
            if self._enter_room is not None
            else self.enter_room(sid, room)
        )
        if hasattr(result, "__await__"):
            await result

    async def _leave(self, sid: str, room: str) -> None:
        result = (
            self._leave_room(sid, room)
            if self._leave_room is not None
            else self.leave_room(sid, room)
        )
        if hasattr(result, "__await__"):
            await result

    @property
    def session_factory(self) -> sessionmaker[Session]:
        value = self._session_factory or self._state("session_factory")
        return cast(sessionmaker[Session], value)

    @property
    def task_service(self) -> TaskService:
        value = self._task_service or self._state("task_service")
        return cast(TaskService, value)

    @property
    def execution(self) -> TaskExecutionService:
        value = self._execution or self._state("task_execution_service")
        return cast(TaskExecutionService, value)

    @property
    def stream_store(self) -> ChatStreamStore:
        value = self._stream_store or self._state("chat_stream_store")
        return cast(ChatStreamStore, value)

    @property
    def emitter(self) -> Any:
        return self._emitter or self._state("chat_emitter")

    def _state(self, name: str) -> object:
        if self.app is None:
            raise RuntimeError("socket_service_unavailable")
        value = getattr(self.app.state, name, None)
        if value is None:
            raise RuntimeError("socket_service_unavailable")
        return value


def register_chat_namespace(
    sio: socketio.AsyncServer,
    app: FastAPI,
) -> ChatNamespace:
    namespace = ChatNamespace(app=app)
    sio.register_namespace(namespace)
    app.state.socket_server = sio
    app.state.chat_namespace = namespace
    return namespace


def _connection_error(code: str) -> socketio.exceptions.ConnectionRefusedError:
    return socketio.exceptions.ConnectionRefusedError(
        "Connection rejected.",
        {"code": code},
    )


def _error_ack(error: Exception) -> dict[str, object]:
    if isinstance(error, ValidationError):
        code = "invalid_payload"
    elif isinstance(error, PermissionError):
        code = "auth_required"
    elif isinstance(error, TaskServiceError):
        code = "access_denied" if error.code == "task_not_found" else error.code
    elif isinstance(error, HTTPException) and isinstance(error.detail, dict):
        raw_code = error.detail.get("code")
        code = raw_code if isinstance(raw_code, str) and raw_code else "request_failed"
        if error.status_code == 404:
            code = "access_denied"
    elif isinstance(error, ChatStreamStaleGeneration):
        code = "stale_generation"
    else:
        code = "request_failed"
        logger.warning(
            "socket_request_failed",
            extra={"exception_type": type(error).__name__},
            exc_info=False,
        )
    return SocketErrorAck(error=SocketErrorDetail(code=code)).model_dump(
        mode="json"
    )


def _next_or_end(
    iterator: Iterator[TaskExecutionEvent],
) -> TaskExecutionEvent | object:
    try:
        return next(iterator)
    except StopIteration:
        return _ITERATOR_END


async def _advance_iterator(
    iterator: Iterator[TaskExecutionEvent],
) -> TaskExecutionEvent | object:
    return await _run_thread_shielded(_next_or_end, iterator)


async def _prime_and_close_iterator(
    iterator: Iterator[TaskExecutionEvent],
) -> None:
    try:
        await _advance_iterator(iterator)
    finally:
        await _close_iterator_async(iterator)


async def _close_execution_iterator(
    iterator: Iterator[TaskExecutionEvent],
    *,
    primed: bool,
) -> None:
    if primed:
        await _close_iterator_async(iterator)
        return
    await _prime_and_close_iterator(iterator)


async def _close_iterator_async(iterator: object) -> None:
    await _run_thread_shielded(_close_iterator, iterator)


async def _run_thread_shielded(
    function: Callable[..., Any],
    *args: object,
    **kwargs: object,
) -> Any:
    work = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancellation: asyncio.CancelledError | None = None
    while not work.done():
        try:
            await asyncio.shield(work)
        except asyncio.CancelledError as error:
            if cancellation is None:
                cancellation = error
        except BaseException:
            break
    if cancellation is not None:
        try:
            work.result()
        except BaseException as error:
            cancellation.add_note(
                f"shielded worker failed with {type(error).__name__}"
            )
        raise cancellation
    return work.result()


def _close_iterator(iterator: object) -> None:
    close = getattr(iterator, "close", None)
    if callable(close):
        close()


def _log_background_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        logger.error(
            "socket_execution_failed",
            extra={"exception_type": type(error).__name__},
            exc_info=False,
        )


def _safe_log(event: str, error: BaseException) -> None:
    logger.warning(
        event,
        extra={"exception_type": type(error).__name__},
        exc_info=False,
    )


def _task_room(task_id: int) -> str:
    return f"task:{task_id}"


def _user_room(user_id: int) -> str:
    return f"user:{user_id}"
