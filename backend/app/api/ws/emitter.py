from __future__ import annotations

from copy import deepcopy
from typing import cast

import socketio

from app.schemas.socket_events import (
    ChatBlockCreatedPayload,
    ChatBlockUpdatedPayload,
    ChatCancelledPayload,
    ChatChunkPayload,
    ChatDonePayload,
    ChatErrorPayload,
    ChatStartPayload,
    ChatStatusUpdatedPayload,
    TaskBriefPayload,
    TaskCreatedPayload,
    TaskStatusPayload,
)
from app.services.chat_blocks import copy_chat_block
from app.services.chat_stream_store import ChatStreamStore
from app.services.task_execution_service import TaskExecutionEvent


class WebSocketChatEmitter:
    def __init__(
        self,
        *,
        sio: socketio.AsyncServer,
        stream_store: ChatStreamStore,
    ) -> None:
        self.sio = sio
        self.stream_store = stream_store

    async def emit_execution_event(
        self,
        event: TaskExecutionEvent,
        *,
        generation_id: str,
    ) -> None:
        task_id = _event_id(event, "task_id")
        subtask_id = _event_id(event, "subtask_id")
        if event.type == "start":
            await self.stream_store.validate_generation(
                task_id=task_id,
                subtask_id=subtask_id,
                generation_id=generation_id,
            )
            await self._emit(
                "chat:start",
                ChatStartPayload(
                    task_id=task_id,
                    subtask_id=subtask_id,
                    generation_id=generation_id,
                ),
                room=_task_room(task_id),
            )
            return
        if event.type == "chunk":
            payload = ChatChunkPayload(
                task_id=task_id,
                subtask_id=subtask_id,
                generation_id=generation_id,
                block_id=_event_text(event, "block_id"),
                block_offset=_event_offset(event, "block_offset"),
                offset=_event_offset(event, "offset"),
                content=_event_text(event, "content"),
            )
            await self.stream_store.append_text(
                subtask_id=subtask_id,
                generation_id=generation_id,
                block_id=payload.block_id,
                offset=payload.offset,
                content=payload.content,
            )
            await self._emit("chat:chunk", payload, room=_task_room(task_id))
            return
        if event.type in {"block_created", "block_updated"}:
            block = dict(copy_chat_block(event.data.get("block")))
            await self.stream_store.upsert_block(
                subtask_id=subtask_id,
                generation_id=generation_id,
                block=block,
            )
            if event.type == "block_created":
                await self._emit(
                    "chat:block_created",
                    ChatBlockCreatedPayload(
                        task_id=task_id,
                        subtask_id=subtask_id,
                        generation_id=generation_id,
                        block=block,
                    ),
                    room=_task_room(task_id),
                )
                return
            await self._emit(
                "chat:block_updated",
                _block_update_payload(
                    task_id=task_id,
                    subtask_id=subtask_id,
                    generation_id=generation_id,
                    block=block,
                ),
                room=_task_room(task_id),
            )
            return
        # TaskExecutionService persists terminal MySQL state before yielding its
        # terminal event. Validate generation ownership, publish that durable
        # outcome, and only then clear its recovery state.
        await self.stream_store.validate_generation(
            task_id=task_id,
            subtask_id=subtask_id,
            generation_id=generation_id,
        )
        try:
            if event.type == "done":
                await self._emit(
                    "chat:done",
                    ChatDonePayload(
                        task_id=task_id,
                        subtask_id=subtask_id,
                        generation_id=generation_id,
                        result=_event_result(event),
                    ),
                    room=_task_room(task_id),
                )
            elif event.type == "error":
                await self._emit(
                    "chat:error",
                    ChatErrorPayload(
                        task_id=task_id,
                        subtask_id=subtask_id,
                        generation_id=generation_id,
                        code=_event_text(event, "code"),
                        result=_event_optional_result(event),
                    ),
                    room=_task_room(task_id),
                )
            elif event.type == "cancelled":
                await self._emit(
                    "chat:cancelled",
                    ChatCancelledPayload(
                        task_id=task_id,
                        subtask_id=subtask_id,
                        generation_id=generation_id,
                        result=_event_optional_result(event),
                    ),
                    room=_task_room(task_id),
                )
            else:  # pragma: no cover - fail-closed if the service grows events
                raise ValueError("socket_execution_event_invalid")
        finally:
            # Even a transport failure must not leave a durable terminal turn
            # advertised as active until its Redis TTL expires.
            if event.type in {"done", "error", "cancelled"}:
                await self.stream_store.finalize(
                    task_id=task_id,
                    subtask_id=subtask_id,
                    generation_id=generation_id,
                )
    async def emit_status_updated(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
        status: dict[str, object],
    ) -> None:
        payload = ChatStatusUpdatedPayload(
            task_id=task_id,
            subtask_id=subtask_id,
            generation_id=generation_id,
            status=deepcopy(status),
        )
        await self.stream_store.set_status_snapshot(
            subtask_id=subtask_id,
            generation_id=generation_id,
            payload=payload.status,
        )
        await self._emit(
            "chat:status_updated", payload, room=_task_room(task_id)
        )

    async def emit_task_created(
        self,
        *,
        user_id: int,
        task: TaskBriefPayload,
    ) -> None:
        await self._emit(
            "task:created",
            TaskCreatedPayload(task=task),
            room=_user_room(user_id),
        )

    async def emit_task_status(
        self,
        *,
        user_id: int,
        task: TaskBriefPayload,
    ) -> None:
        payload = TaskStatusPayload(task=task)
        await self._emit("task:status", payload, room=_task_room(task.id))
        await self._emit("task:status", payload, room=_user_room(user_id))

    async def _emit(
        self,
        event: str,
        payload: object,
        *,
        room: str,
    ) -> None:
        model_dump = getattr(payload, "model_dump")
        await self.sio.emit(
            event,
            model_dump(mode="json"),
            room=room,
            namespace="/chat",
        )


def _task_room(task_id: int) -> str:
    return f"task:{task_id}"


def _user_room(user_id: int) -> str:
    return f"user:{user_id}"


def _event_id(event: TaskExecutionEvent, key: str) -> int:
    value = event.data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError("socket_execution_event_invalid")
    return value


def _event_offset(event: TaskExecutionEvent, key: str) -> int:
    value = event.data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("socket_execution_event_invalid")
    return value


def _event_text(event: TaskExecutionEvent, key: str) -> str:
    value = event.data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError("socket_execution_event_invalid")
    return value


def _event_result(event: TaskExecutionEvent) -> dict[str, object]:
    result = event.data.get("result")
    if not isinstance(result, dict):
        raise ValueError("socket_execution_event_invalid")
    return cast(dict[str, object], deepcopy(result))


def _event_optional_result(
    event: TaskExecutionEvent,
) -> dict[str, object] | None:
    result = event.data.get("result")
    return _event_result(event) if result is not None else None


def _block_update_payload(
    *,
    task_id: int,
    subtask_id: int,
    generation_id: str,
    block: dict[str, object],
) -> ChatBlockUpdatedPayload:
    common: dict[str, object] = {
        "task_id": task_id,
        "subtask_id": subtask_id,
        "generation_id": generation_id,
        "block_id": block["id"],
        "status": block["status"],
    }
    if block["type"] == "text":
        common["content"] = block["content"]
    else:
        common["tool_input"] = block["tool_input"]
        if "tool_output" in block:
            common["tool_output"] = block["tool_output"]
    return ChatBlockUpdatedPayload.model_validate(common)
