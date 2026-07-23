from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from threading import Lock
from typing import Literal

from app.services.runtime_types import RuntimeEvent


TerminalOutcome = Literal["COMPLETED", "FAILED", "CANCELLED"]


@dataclass(slots=True)
class TaskExecutionState:
    user_id: int
    task_id: int
    assistant_subtask_id: int
    _lock: Lock = field(default_factory=Lock, repr=False)
    _cancel_requested: bool = False
    _terminal: TerminalOutcome | None = None
    _runtime_stream: Iterator[RuntimeEvent] | None = None

    def request_cancel(self) -> tuple[bool, Iterator[RuntimeEvent] | None]:
        with self._lock:
            if self._terminal is not None or self._cancel_requested:
                return False, None
            self._cancel_requested = True
            return True, self._runtime_stream

    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def attach_stream(self, stream: Iterator[RuntimeEvent]) -> bool:
        with self._lock:
            if self._terminal is not None:
                return False
            self._runtime_stream = stream
            return not self._cancel_requested

    def claim_terminal(self, desired: TerminalOutcome) -> TerminalOutcome | None:
        with self._lock:
            if self._terminal is not None:
                return None
            outcome: TerminalOutcome = (
                "CANCELLED" if self._cancel_requested else desired
            )
            self._terminal = outcome
            return outcome


class TaskExecutionCancellationRegistry:
    def __init__(self) -> None:
        self._lock = Lock()
        self._by_task: dict[int, TaskExecutionState] = {}

    def prepare(
        self,
        *,
        user_id: int,
        task_id: int,
        assistant_subtask_id: int,
    ) -> TaskExecutionState:
        state = TaskExecutionState(
            user_id=user_id,
            task_id=task_id,
            assistant_subtask_id=assistant_subtask_id,
        )
        with self._lock:
            self._by_task[task_id] = state
        return state

    def claim(
        self,
        *,
        user_id: int,
        task_id: int,
        assistant_subtask_id: int,
    ) -> TaskExecutionState:
        with self._lock:
            state = self._by_task.get(task_id)
            if (
                state is None
                or state.user_id != user_id
                or state.assistant_subtask_id != assistant_subtask_id
            ):
                state = TaskExecutionState(
                    user_id=user_id,
                    task_id=task_id,
                    assistant_subtask_id=assistant_subtask_id,
                )
                self._by_task[task_id] = state
            return state

    def request_cancel(
        self,
        *,
        user_id: int,
        task_id: int,
    ) -> tuple[bool, Iterator[RuntimeEvent] | None]:
        with self._lock:
            state = self._by_task.get(task_id)
            if state is None or state.user_id != user_id:
                return False, None
        return state.request_cancel()

    def discard(self, state: TaskExecutionState) -> None:
        with self._lock:
            if self._by_task.get(state.task_id) is state:
                del self._by_task[state.task_id]
