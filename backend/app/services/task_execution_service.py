from __future__ import annotations

from collections.abc import Callable, Iterator
from copy import deepcopy
from dataclasses import dataclass
import logging
import math
from typing import Literal, Protocol

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import bad_request, conflict, not_found, service_unavailable
from app.core.limits import MAX_RESOURCE_NAME_LENGTH
from app.db import models
from app.db.session import session_scope
from app.repositories.subtask_context_repository import SubtaskContextRepository
from app.repositories.task_repository import TaskRepository, TaskRepositoryError
from app.schemas.chat import ChatSendRequest
from app.schemas.modeling import ModelRef
from app.services.agent_runtime import PreparedRuntimeTurn, RuntimeTurn
from app.services.agent_service import AgentService, ResolvedAgent
from app.services.runtime_context_projection import (
    TaskExecutionError,
    project_runtime_contexts,
)
from app.services.runtime_event_reducer import (
    ResultEnvelopeError,
    RuntimeEventReducer,
    RuntimeEventReductionError,
)
from app.services.runtime_types import (
    CapabilityContext,
    ProviderCallMetrics,
    RuntimeObserver,
    RuntimeAssistantTurn,
    RuntimeTaskTurn,
    RuntimeEvent,
    RuntimeTerminalError,
    RuntimeUserContext,
    RuntimeUserTurn,
)
from app.services.subtask_context_service import (
    SubtaskContextService,
    SubtaskContextServiceError,
)
from app.services.subtask_history import SubtaskHistoryProjector
from app.services.task_execution_cancellation import (
    TaskExecutionCancellationRegistry,
    TaskExecutionState,
    TerminalOutcome,
)


logger = logging.getLogger(__name__)


class RuntimeLike(Protocol):
    def prepare_turn(self, turn: RuntimeTurn) -> PreparedRuntimeTurn: ...

    def stream_turn(
        self,
        turn: PreparedRuntimeTurn,
        *,
        observer: Callable[[ProviderCallMetrics], None],
    ) -> Iterator[RuntimeEvent]: ...


@dataclass(frozen=True, slots=True)
class PreparedTaskExecution:
    task_id: int
    user_subtask_id: int | None
    user_message_id: int | None
    assistant_subtask_id: int
    runtime_turn: PreparedRuntimeTurn
    provider: str
    model: str


TaskExecutionEventType = Literal[
    "start",
    "chunk",
    "block_created",
    "block_updated",
    "done",
    "error",
    "cancelled",
]


@dataclass(frozen=True, slots=True)
class TaskExecutionEvent:
    type: TaskExecutionEventType
    data: dict[str, object]


class TaskExecutionService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        runtime: RuntimeLike,
        agent_service: AgentService | None = None,
        repository: TaskRepository | None = None,
        contexts: SubtaskContextService | None = None,
        context_repository: SubtaskContextRepository | None = None,
        history_projector: SubtaskHistoryProjector | None = None,
        metrics_observer: RuntimeObserver | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.runtime = runtime
        self.settings = settings or get_settings()
        self.agent_service = agent_service or AgentService(settings=self.settings)
        self.repository = repository or TaskRepository()
        self.contexts = contexts or SubtaskContextService(
            session_factory=session_factory
        )
        self.context_repository = context_repository or self.contexts.repository
        self.history_projector = history_projector or SubtaskHistoryProjector()
        self.metrics_observer = metrics_observer or _log_provider_metrics
        self._cancellations = TaskExecutionCancellationRegistry()

    def prepare_send(
        self,
        *,
        user_id: int,
        request: ChatSendRequest,
    ) -> PreparedTaskExecution:
        with session_scope(self.session_factory) as session:
            if request.task_id is None:
                agent = self._resolve_agent(
                    session,
                    user_id=user_id,
                    agent_id=request.agent_id,
                )
                model = self.agent_service.resolve_model(
                    agent=agent,
                    conversation_override=request.model_override,
                )
                task = self.repository.create_task(
                    session,
                    user_id=user_id,
                    agent_id=agent.id if agent is not None else None,
                    name=_task_title(request.message),
                    model_override=request.model_override,
                )
            else:
                task = self.repository.get(
                    session,
                    user_id=user_id,
                    task_id=request.task_id,
                )
                if task is None:
                    raise not_found("task_not_found", "Task not found.")
                if task.status in {"PENDING", "RUNNING"}:
                    raise conflict(
                        "task_running",
                        "Task has an active generation.",
                    )
                if request.agent_id is not None and request.agent_id != task.agent_id:
                    raise bad_request(
                        "agent_locked",
                        "Agent cannot be changed in this task.",
                    )
                if request.model_override is not None:
                    raise bad_request(
                        "model_override_not_allowed",
                        "Use the task model setting before sending a message.",
                    )
                agent = self._resolve_agent(
                    session,
                    user_id=user_id,
                    agent_id=task.agent_id,
                )
                model = self.agent_service.resolve_model(
                    agent=agent,
                    conversation_override=_task_model_override(task),
                )

            user_subtask, assistant = self.repository.create_turn(
                session,
                task=task,
                prompt=request.message,
            )
            try:
                self.contexts.bind_drafts(
                    session,
                    user_id=user_id,
                    context_ids=request.context_ids,
                    subtask_id=user_subtask.id,
                )
            except SubtaskContextServiceError as error:
                raise conflict(
                    error.code,
                    "One or more contexts are unavailable.",
                ) from error

            prepared = self._prepare_runtime(
                session,
                user_id=user_id,
                task=task,
                cutoff_message_id=user_subtask.message_id,
                current_user_subtask_id=user_subtask.id,
                agent=agent,
                provider=model.provider,
                model=model.model,
            )
            result = PreparedTaskExecution(
                task_id=task.id,
                user_subtask_id=user_subtask.id,
                user_message_id=user_subtask.message_id,
                assistant_subtask_id=assistant.id,
                runtime_turn=prepared,
                provider=model.provider,
                model=model.model,
            )
        self._cancellations.prepare(
            user_id=user_id,
            task_id=result.task_id,
            assistant_subtask_id=result.assistant_subtask_id,
        )
        return result

    def prepare_retry(
        self,
        *,
        user_id: int,
        task_id: int,
        subtask_id: int,
    ) -> PreparedTaskExecution:
        with session_scope(self.session_factory) as session:
            task = self.repository.get(
                session,
                user_id=user_id,
                task_id=task_id,
            )
            if task is None:
                raise not_found("task_not_found", "Task not found.")
            if task.status in {"PENDING", "RUNNING"}:
                raise conflict("task_running", "Task has an active generation.")
            try:
                assistant = self.repository.reset_failed_assistant(
                    session,
                    user_id=user_id,
                    task_id=task_id,
                    subtask_id=subtask_id,
                )
            except TaskRepositoryError as error:
                raise _repository_http_error(error) from error

            parent_user = session.scalar(
                select(models.Subtask)
                .where(
                    models.Subtask.task_id == task.id,
                    models.Subtask.user_id == user_id,
                    models.Subtask.role == "USER",
                    models.Subtask.message_id == assistant.parent_id,
                )
                .order_by(models.Subtask.id.desc())
                .limit(1)
            )
            if parent_user is None:
                raise conflict(
                    "subtask_parent_not_found",
                    "The failed assistant has no retryable parent message.",
                )

            agent = self._resolve_agent(
                session,
                user_id=user_id,
                agent_id=task.agent_id,
            )
            model = self.agent_service.resolve_model(
                agent=agent,
                conversation_override=_task_model_override(task),
            )
            prepared = self._prepare_runtime(
                session,
                user_id=user_id,
                task=task,
                cutoff_message_id=assistant.message_id,
                current_user_subtask_id=parent_user.id,
                agent=agent,
                provider=model.provider,
                model=model.model,
            )
            result = PreparedTaskExecution(
                task_id=task.id,
                user_subtask_id=None,
                user_message_id=None,
                assistant_subtask_id=assistant.id,
                runtime_turn=prepared,
                provider=model.provider,
                model=model.model,
            )
        self._cancellations.prepare(
            user_id=user_id,
            task_id=result.task_id,
            assistant_subtask_id=result.assistant_subtask_id,
        )
        return result

    def execute(
        self,
        prepared: PreparedTaskExecution,
    ) -> Iterator[TaskExecutionEvent]:
        user_id = prepared.runtime_turn.context.user_id
        state = self._cancellations.claim(
            user_id=user_id,
            task_id=prepared.task_id,
            assistant_subtask_id=prepared.assistant_subtask_id,
        )
        reducer = RuntimeEventReducer(provider=prepared.provider, model=prepared.model)
        runtime_stream: Iterator[RuntimeEvent] | None = None
        started = False

        try:
            if state.cancel_requested():
                event = self._finish_execution(
                    state=state,
                    prepared=prepared,
                    user_id=user_id,
                    desired="CANCELLED",
                    reducer=reducer,
                )
                if event is not None:
                    yield event
                return

            with session_scope(self.session_factory) as session:
                self.repository.mark_running(
                    session,
                    user_id=user_id,
                    subtask_id=prepared.assistant_subtask_id,
                )
            started = True
            yield self._event("start", prepared, status="RUNNING")

            if state.cancel_requested():
                event = self._finish_execution(
                    state=state,
                    prepared=prepared,
                    user_id=user_id,
                    desired="CANCELLED",
                    reducer=reducer,
                )
                if event is not None:
                    yield event
                return

            runtime_stream = self.runtime.stream_turn(
                prepared.runtime_turn,
                observer=self._observe_provider_metrics,
            )
            if not state.attach_stream(runtime_stream):
                event = self._finish_execution(
                    state=state,
                    prepared=prepared,
                    user_id=user_id,
                    desired="CANCELLED",
                    reducer=reducer,
                )
                if event is not None:
                    yield event
                return

            while True:
                if state.cancel_requested():
                    break
                try:
                    runtime_event = next(runtime_stream)
                except StopIteration:
                    break
                if state.cancel_requested():
                    break
                emissions = reducer.accept(runtime_event)
                if state.cancel_requested():
                    break
                for emission in emissions:
                    yield self._event(emission.type, prepared, **emission.data)
                    if state.cancel_requested():
                        break
                if state.cancel_requested():
                    break

            desired: TerminalOutcome = (
                "CANCELLED" if state.cancel_requested() else "COMPLETED"
            )
            event = self._finish_execution(
                state=state,
                prepared=prepared,
                user_id=user_id,
                desired=desired,
                reducer=reducer,
            )
            if event is None:
                raise RuntimeError("execution_terminal_claim_lost")
            yield event
        except Exception as error:
            event = self._finish_execution(
                state=state,
                prepared=prepared,
                user_id=user_id,
                desired="FAILED",
                reducer=reducer,
                error_code=_safe_execution_error_code(error),
            )
            if event is None:
                raise
            yield event
        except GeneratorExit:
            if started:
                self._finish_closed_safely(
                    state=state,
                    prepared=prepared,
                    user_id=user_id,
                    reducer=reducer,
                )
            raise
        except BaseException:
            if started:
                self._finish_closed_safely(
                    state=state,
                    prepared=prepared,
                    user_id=user_id,
                    reducer=reducer,
                )
            raise
        finally:
            try:
                _close_stream_safely(runtime_stream)
            finally:
                self._cancellations.discard(state)

    def cancel(self, *, user_id: int, task_id: int) -> bool:
        won, stream = self._cancellations.request_cancel(
            user_id=user_id,
            task_id=task_id,
        )
        if won:
            _close_stream_safely(stream)
        return won

    def request_cancel(self, *, user_id: int, task_id: int) -> bool:
        return self.cancel(user_id=user_id, task_id=task_id)

    def recover_interrupted(self) -> int:
        with session_scope(self.session_factory) as session:
            return self.repository.recover_interrupted(session)

    def _prepare_runtime(
        self,
        session: Session,
        *,
        user_id: int,
        task: models.Task,
        cutoff_message_id: int,
        current_user_subtask_id: int,
        agent: ResolvedAgent | None,
        provider: str,
        model: str,
    ) -> PreparedRuntimeTurn:
        turns = self._runtime_turns(
            session,
            user_id=user_id,
            task_id=task.id,
            cutoff_message_id=cutoff_message_id,
            current_user_subtask_id=current_user_subtask_id,
        )
        resolved = agent or self.agent_service.plain_chat_agent()
        context = CapabilityContext(
            user_id=user_id,
            agent_config=resolved.config,
            session_factory=self.session_factory,
            token_budget=self.settings.chat_context_token_budget,
        )
        return self.runtime.prepare_turn(
            RuntimeTurn(
                context=context,
                agent_prompt=resolved.config.system_prompt,
                provider=provider,
                model=model,
                turns=turns,
            )
        )

    def _runtime_turns(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        cutoff_message_id: int,
        current_user_subtask_id: int,
    ) -> tuple[RuntimeTaskTurn, ...]:
        subtasks = [
            subtask
            for subtask in self.repository.list_subtasks(
                session,
                user_id=user_id,
                task_id=task_id,
            )
            if subtask.message_id <= cutoff_message_id
        ]
        user_subtask_ids = [
            subtask.id for subtask in subtasks if subtask.role == "USER"
        ]
        context_rows = self.context_repository.list_runtime_for_subtasks(
            session,
            user_id=user_id,
            subtask_ids=user_subtask_ids,
        )
        contexts_by_subtask: dict[int, list[RuntimeUserContext]] = {}
        for row in context_rows:
            projected = project_runtime_contexts(
                row,
                is_current=row.subtask_id == current_user_subtask_id,
            )
            if projected:
                contexts_by_subtask.setdefault(row.subtask_id, []).extend(projected)
        turns: list[RuntimeTaskTurn] = []
        current_user_message_id: int | None = None
        for subtask in subtasks:
            if subtask.role == "USER":
                projected = self.history_projector.project_user(subtask)
                content = projected[0].get("content", "")
                turns.append(
                    RuntimeTaskTurn(
                        user=RuntimeUserTurn(
                            message_id=str(subtask.id),
                            text=content if isinstance(content, str) else "",
                            contexts=tuple(contexts_by_subtask.get(subtask.id, ())),
                        )
                    )
                )
                current_user_message_id = subtask.message_id
                continue
            if subtask.role != "ASSISTANT" or not turns:
                continue
            if subtask.parent_id != current_user_message_id:
                raise TaskExecutionError("history_invalid")
            projected = self.history_projector.project_assistant(subtask)
            assistants = tuple(
                RuntimeAssistantTurn(
                    message_id=f"{subtask.id}:{index}",
                    text=text,
                )
                for index, message in enumerate(projected)
                if message.get("role") == "assistant"
                if (text := _projected_assistant_text(message.get("content")))
            )
            if assistants:
                previous = turns[-1]
                turns[-1] = RuntimeTaskTurn(
                    user=previous.user,
                    assistants=(*previous.assistants, *assistants),
                )
        return tuple(turns)

    def _resolve_agent(
        self,
        session: Session,
        *,
        user_id: int,
        agent_id: str | None,
    ) -> ResolvedAgent | None:
        if agent_id is None:
            return None
        return self.agent_service.resolve_for_turn(
            session,
            user_id=user_id,
            agent_id=agent_id,
        )

    def _finish_execution(
        self,
        *,
        state: TaskExecutionState,
        prepared: PreparedTaskExecution,
        user_id: int,
        desired: TerminalOutcome,
        reducer: RuntimeEventReducer,
        error_code: str = "provider_call_failed",
    ) -> TaskExecutionEvent | None:
        result = (
            reducer.finish_success()
            if desired == "COMPLETED"
            else reducer.partial_result()
        )
        outcome = state.claim_terminal(desired)
        if outcome is None:
            return None
        if outcome == "CANCELLED" and desired != "CANCELLED":
            result = reducer.partial_result()
        if outcome == "COMPLETED":
            try:
                with session_scope(self.session_factory) as session:
                    self.repository.finish_assistant(
                        session,
                        user_id=user_id,
                        subtask_id=prepared.assistant_subtask_id,
                        status="COMPLETED",
                        result=result,
                        error_message=None,
                    )
            except Exception as error:
                return self._terminal_persistence_failed(
                    prepared=prepared,
                    user_id=user_id,
                    reducer=reducer,
                    original_error=error,
                )
            return self._event("done", prepared, result=deepcopy(result))
        if outcome == "CANCELLED":
            try:
                self._persist_cancelled(prepared, user_id=user_id, result=result)
            except Exception as error:
                return self._terminal_persistence_failed(
                    prepared=prepared,
                    user_id=user_id,
                    reducer=reducer,
                    original_error=error,
                )
            return self._event("cancelled", prepared, result=deepcopy(result))
        self._persist_failed(
            prepared,
            user_id=user_id,
            result=result,
            error_code=error_code,
        )
        return self._event(
            "error",
            prepared,
            code=error_code,
            result=deepcopy(result),
        )

    def _terminal_persistence_failed(
        self,
        *,
        prepared: PreparedTaskExecution,
        user_id: int,
        reducer: RuntimeEventReducer,
        original_error: Exception,
    ) -> TaskExecutionEvent:
        error_code = "generation_persistence_failed"
        result = reducer.partial_result()
        logger.error(
            "generation_terminal_persistence_failed",
            extra={
                "exception_type": type(original_error).__name__,
                "error_code": error_code,
            },
            exc_info=False,
        )
        try:
            with session_scope(self.session_factory) as session:
                self.repository.fail_active_assistant(
                    session,
                    user_id=user_id,
                    task_id=prepared.task_id,
                    subtask_id=prepared.assistant_subtask_id,
                    result=result,
                    error_message=error_code,
                )
        except Exception as fallback_error:
            logger.error(
                "generation_persistence_fallback_failed",
                extra={
                    "exception_type": type(fallback_error).__name__,
                    "error_code": error_code,
                },
                exc_info=False,
            )
            raise original_error from None
        return self._event(
            "error",
            prepared,
            code=error_code,
            result=deepcopy(result),
        )

    def _finish_closed_safely(
        self,
        *,
        state: TaskExecutionState,
        prepared: PreparedTaskExecution,
        user_id: int,
        reducer: RuntimeEventReducer,
    ) -> None:
        try:
            self._finish_execution(
                state=state,
                prepared=prepared,
                user_id=user_id,
                desired="FAILED",
                reducer=reducer,
                error_code="generation_closed",
            )
        except BaseException as error:
            logger.error(
                "generation_close_persistence_failed",
                extra={"exception_type": type(error).__name__},
                exc_info=False,
            )

    def _observe_provider_metrics(self, metrics: ProviderCallMetrics) -> None:
        try:
            self.metrics_observer(metrics)
        except Exception as error:
            logger.warning(
                "provider_metrics_observer_failed",
                extra={"exception_type": type(error).__name__},
                exc_info=False,
            )

    def _persist_failed(
        self,
        prepared: PreparedTaskExecution,
        *,
        user_id: int,
        result: dict[str, object],
        error_code: str,
    ) -> None:
        with session_scope(self.session_factory) as session:
            self.repository.finish_assistant(
                session,
                user_id=user_id,
                subtask_id=prepared.assistant_subtask_id,
                status="FAILED",
                result=result,
                error_message=error_code,
            )

    def _persist_cancelled(
        self,
        prepared: PreparedTaskExecution,
        *,
        user_id: int,
        result: dict[str, object],
    ) -> None:
        with session_scope(self.session_factory) as session:
            self.repository.cancel_assistant(
                session,
                user_id=user_id,
                subtask_id=prepared.assistant_subtask_id,
                result=result,
            )

    @staticmethod
    def _event(
        event_type: TaskExecutionEventType,
        prepared: PreparedTaskExecution,
        **data: object,
    ) -> TaskExecutionEvent:
        return TaskExecutionEvent(
            type=event_type,
            data={
                "task_id": prepared.task_id,
                "subtask_id": prepared.assistant_subtask_id,
                **data,
            },
        )


def _task_title(message: str) -> str:
    title = " ".join(message.strip().split())
    return title[:MAX_RESOURCE_NAME_LENGTH] or "New task"


def _task_model_override(task: models.Task) -> ModelRef | None:
    if task.model_override_json is None:
        return None
    try:
        return ModelRef.model_validate(task.model_override_json)
    except ValidationError:
        raise service_unavailable(
            "model_unavailable",
            "The configured model is unavailable.",
        ) from None


def _repository_http_error(error: TaskRepositoryError) -> HTTPException:
    if error.code in {"task_not_found", "subtask_not_found"}:
        return not_found(error.code, "Task or subtask not found.")
    if error.code in {"subtask_not_failed", "subtask_invalid_status"}:
        return conflict(error.code, "Subtask status does not allow this operation.")
    return bad_request(error.code, "Subtask cannot be retried.")


def _safe_execution_error_code(error: Exception) -> str:
    if isinstance(error, (RuntimeEventReductionError, ResultEnvelopeError)):
        return "runtime_output_invalid"
    if isinstance(error, RuntimeTerminalError):
        return error.code
    if isinstance(error, HTTPException) and isinstance(error.detail, dict):
        code = error.detail.get("code")
        if isinstance(code, str) and code:
            return code
    if isinstance(error, ValueError):
        message = str(error)
        if message.startswith(("messages_chain_", "chat_block_")):
            return "runtime_output_invalid"
    return "provider_call_failed"


def _projected_assistant_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    values: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        text = block.get("text")
        if isinstance(text, str):
            values.append(text)
    return "".join(values)


def _close_stream_safely(stream: Iterator[RuntimeEvent] | None) -> None:
    if stream is None:
        return
    close = getattr(stream, "close", None)
    if not callable(close):
        return
    try:
        close()
    except BaseException as error:
        logger.warning(
            "runtime_stream_close_failed",
            extra={"exception_type": type(error).__name__},
            exc_info=False,
        )


def _log_provider_metrics(metrics: ProviderCallMetrics) -> None:
    if not isinstance(metrics, ProviderCallMetrics):
        logger.warning("provider_metrics_invalid")
        return
    provider = _safe_metric_text(metrics.provider)
    model = _safe_metric_text(metrics.model)
    if provider is None or model is None or metrics.status not in {"completed", "failed"}:
        logger.warning("provider_metrics_invalid")
        return
    provider_request_id = _safe_metric_text(metrics.provider_request_id)
    logger.info(
        "provider_call_metrics",
        extra={
            "call_index": _safe_metric_count(metrics.call_index),
            "provider": provider,
            "model": model,
            "provider_request_id": provider_request_id,
            "input_tokens": _safe_metric_count(metrics.input_tokens),
            "output_tokens": _safe_metric_count(metrics.output_tokens),
            "duration_ms": _safe_metric_duration(metrics.duration_ms),
            "provider_status": metrics.status,
        },
    )


def _safe_metric_text(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return None
    return value


def _safe_metric_count(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_metric_duration(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None
