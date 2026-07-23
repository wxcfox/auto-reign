from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from app.services.chat_blocks import copy_chat_block
from app.services.chat_stream_types import (
    ActiveStreamSnapshot,
    ChatStreamNotActive,
    ChatStreamOffsetMismatch,
    ChatStreamStaleGeneration,
    _block_id,
    _generation_id,
    _json_object,
    _offset,
    _positive_id,
    _text,
    _timestamp,
    _utc_now,
)
from app.services.text_offsets import advance_utf16_offset


@dataclass
class _MemoryState:
    task_id: int
    subtask_id: int
    generation_id: str
    offset: int
    cached_content: str
    blocks: list[dict[str, object]]
    started_at: str
    last_activity_at: str
    status_updated: dict[str, object] | None
    cancelled: bool
    expires_at: datetime


class MemoryChatStreamStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = 3600,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("chat_stream_invalid_ttl")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()
        self._active_by_task: dict[int, int] = {}
        self._states: dict[int, _MemoryState] = {}

    async def start(self, *, task_id: int, subtask_id: int) -> str:
        _positive_id(task_id, "task_id")
        _positive_id(subtask_id, "subtask_id")
        async with self._lock:
            generation_id = str(uuid4())
            now = _utc_now(self._clock)
            self._cleanup_expired(now)
            old_subtask_id = self._active_by_task.get(task_id)
            if old_subtask_id is not None:
                self._states.pop(old_subtask_id, None)
            old_state = self._states.pop(subtask_id, None)
            if old_state is not None and self._active_by_task.get(old_state.task_id) == subtask_id:
                self._active_by_task.pop(old_state.task_id, None)
            timestamp = _timestamp(now)
            self._states[subtask_id] = _MemoryState(
                task_id=task_id,
                subtask_id=subtask_id,
                generation_id=generation_id,
                offset=0,
                cached_content="",
                blocks=[],
                started_at=timestamp,
                last_activity_at=timestamp,
                status_updated=None,
                cancelled=False,
                expires_at=now + self._ttl,
            )
            self._active_by_task[task_id] = subtask_id
            return generation_id

    async def get_active(self, *, task_id: int) -> ActiveStreamSnapshot | None:
        _positive_id(task_id, "task_id")
        async with self._lock:
            self._cleanup_expired(_utc_now(self._clock))
            subtask_id = self._active_by_task.get(task_id)
            if subtask_id is None:
                return None
            state = self._states.get(subtask_id)
            if state is None or state.task_id != task_id:
                self._active_by_task.pop(task_id, None)
                return None
            return _snapshot(state)

    async def validate_generation(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
    ) -> None:
        _positive_id(task_id, "task_id")
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        async with self._lock:
            state = self._active_state(subtask_id, _utc_now(self._clock))
            if state.task_id != task_id:
                raise ChatStreamNotActive()
            self._require_generation(state, generation_id)

    async def append_text(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        block_id: str,
        offset: int,
        content: str,
    ) -> int:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        _block_id(block_id)
        _offset(offset)
        _text(content)
        async with self._lock:
            now = _utc_now(self._clock)
            state = self._active_state(subtask_id, now)
            self._require_generation(state, generation_id)
            if state.offset != offset:
                raise ChatStreamOffsetMismatch()
            state.cached_content += content
            state.offset = advance_utf16_offset(state.offset, content)
            self._touch(state, now)
            return state.offset

    async def upsert_block(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        block: dict[str, object],
    ) -> None:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        canonical = dict(copy_chat_block(block))
        block_id = cast(str, canonical["id"])
        async with self._lock:
            now = _utc_now(self._clock)
            state = self._active_state(subtask_id, now)
            self._require_generation(state, generation_id)
            for index, current in enumerate(state.blocks):
                if current["id"] == block_id:
                    state.blocks[index] = canonical
                    break
            else:
                state.blocks.append(canonical)
            self._touch(state, now)

    async def set_cancelled(self, *, subtask_id: int, generation_id: str) -> None:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        async with self._lock:
            now = _utc_now(self._clock)
            state = self._active_state(subtask_id, now)
            self._require_generation(state, generation_id)
            state.cancelled = True
            self._touch(state, now)

    async def is_cancelled(self, *, subtask_id: int, generation_id: str) -> bool:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        async with self._lock:
            now = _utc_now(self._clock)
            state = self._states.get(subtask_id)
            if state is None:
                return False
            if state.expires_at <= now:
                self._remove_state(state)
                return False
            self._require_generation(state, generation_id)
            return state.cancelled

    async def set_status_snapshot(
        self,
        *,
        subtask_id: int,
        generation_id: str,
        payload: dict[str, object],
    ) -> None:
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        canonical = _json_object(payload)
        async with self._lock:
            now = _utc_now(self._clock)
            state = self._active_state(subtask_id, now)
            self._require_generation(state, generation_id)
            state.status_updated = canonical
            self._touch(state, now)

    async def finalize(
        self,
        *,
        task_id: int,
        subtask_id: int,
        generation_id: str,
    ) -> None:
        _positive_id(task_id, "task_id")
        _positive_id(subtask_id, "subtask_id")
        _generation_id(generation_id)
        async with self._lock:
            self._cleanup_expired(_utc_now(self._clock))
            if self._active_by_task.get(task_id) != subtask_id:
                return
            state = self._states.get(subtask_id)
            if state is None or state.task_id != task_id or state.generation_id != generation_id:
                return
            self._active_by_task.pop(task_id, None)
            self._states.pop(subtask_id, None)

    async def aclose(self) -> None:
        return None

    def _active_state(self, subtask_id: int, now: datetime) -> _MemoryState:
        state = self._states.get(subtask_id)
        if state is None:
            raise ChatStreamNotActive()
        if state.expires_at <= now:
            self._remove_state(state)
            raise ChatStreamNotActive()
        if self._active_by_task.get(state.task_id) != subtask_id:
            raise ChatStreamNotActive()
        return state

    def _touch(self, state: _MemoryState, now: datetime) -> None:
        state.last_activity_at = _timestamp(now)
        state.expires_at = now + self._ttl

    @staticmethod
    def _require_generation(state: _MemoryState, generation_id: str) -> None:
        if state.generation_id != generation_id:
            raise ChatStreamStaleGeneration()

    def _cleanup_expired(self, now: datetime) -> None:
        for state in tuple(self._states.values()):
            if state.expires_at <= now:
                self._remove_state(state)

    def _remove_state(self, state: _MemoryState) -> None:
        self._states.pop(state.subtask_id, None)
        if self._active_by_task.get(state.task_id) == state.subtask_id:
            self._active_by_task.pop(state.task_id, None)


def _snapshot(state: _MemoryState) -> ActiveStreamSnapshot:
    return ActiveStreamSnapshot(
        task_id=state.task_id,
        subtask_id=state.subtask_id,
        generation_id=state.generation_id,
        offset=state.offset,
        cached_content=state.cached_content,
        blocks=tuple(deepcopy(state.blocks)),
        started_at=state.started_at,
        last_activity_at=state.last_activity_at,
        status_updated=deepcopy(state.status_updated),
    )
