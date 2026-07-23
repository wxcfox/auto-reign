from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from copy import deepcopy

from sqlalchemy.orm import Session

from app.core.limits import MAX_RESOURCE_NAME_LENGTH
from app.db import models
from app.repositories.resource_repository import ResourceRepository
from app.repositories.subtask_context_repository import SubtaskContextRepository
from app.repositories.task_repository import TaskRepository, TaskRepositoryError
from app.schemas.modeling import ModelRef
from app.schemas.subtask_contexts import SubtaskContextBrief
from app.schemas.tasks import (
    SubtaskResponse,
    TaskAgentResponse,
    TaskDetailResponse,
    TaskHistoryItemResponse,
)
from app.services.subtask_history import SubtaskHistoryProjector


class TaskServiceError(ValueError):
    """Stable Task-domain error suitable for transport-specific mapping."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class TaskService:
    def __init__(
        self,
        repository: TaskRepository | None = None,
        resource_repository: ResourceRepository | None = None,
        context_repository: SubtaskContextRepository | None = None,
        history_projector: SubtaskHistoryProjector | None = None,
    ) -> None:
        self.repository = repository or TaskRepository()
        self.resource_repository = resource_repository or ResourceRepository()
        self.context_repository = context_repository or SubtaskContextRepository()
        self.history_projector = history_projector or SubtaskHistoryProjector()

    def list_tasks(
        self, session: Session, *, user_id: int, limit: int = 50
    ) -> list[TaskHistoryItemResponse]:
        recent = self.repository.list_recent(session, user_id=user_id, limit=limit)
        agents = self._agents_for_tasks(
            session, user_id=user_id, tasks=[item.task for item in recent]
        )
        return [
            self._history_item(
                item.task,
                agent=agents.get(item.task.agent_id),
                last_message=item.last_prompt or item.task.name,
            )
            for item in recent
        ]

    def get_task(self, session: Session, *, user_id: int, task_id: int) -> TaskDetailResponse:
        task = self._require_task(session, user_id=user_id, task_id=task_id)
        subtasks = self.repository.list_subtasks(session, user_id=user_id, task_id=task.id)
        agent = self._agents_for_tasks(session, user_id=user_id, tasks=[task]).get(task.agent_id)
        history = self._history_item(
            task,
            agent=agent,
            last_message=self._last_user_prompt(subtasks, fallback=task.name),
        )
        return TaskDetailResponse(
            **history.model_dump(),
            subtasks=self._subtask_responses(session, user_id=user_id, subtasks=subtasks),
        )

    def require_task_owner(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
    ) -> None:
        """Validate Task ownership without loading any Subtask history."""
        self._require_task(session, user_id=user_id, task_id=task_id)

    def get_task_brief(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
    ) -> TaskHistoryItemResponse:
        """Project one owner-scoped Task without loading Subtasks or Contexts."""
        task = self._require_task(session, user_id=user_id, task_id=task_id)
        agent = self._agents_for_tasks(
            session,
            user_id=user_id,
            tasks=[task],
        ).get(task.agent_id)
        return self._history_item(task, agent=agent, last_message=task.name)

    def list_subtasks_after(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        after_message_id: int | None,
    ) -> list[SubtaskResponse]:
        # Validate the owner-scoped active Task before consulting the strict
        # cursor query. Socket joins must not expose repository access directly.
        self._require_task(session, user_id=user_id, task_id=task_id)
        subtasks = self.repository.list_subtasks(
            session,
            user_id=user_id,
            task_id=task_id,
            after_message_id=after_message_id,
        )
        return self._subtask_responses(session, user_id=user_id, subtasks=subtasks)

    def rename_task(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        name: str,
    ) -> TaskHistoryItemResponse:
        normalized = name.strip()
        if not normalized or len(normalized) > MAX_RESOURCE_NAME_LENGTH:
            raise TaskServiceError("task_name_invalid")
        try:
            task = self.repository.rename(
                session, user_id=user_id, task_id=task_id, name=normalized
            )
        except TaskRepositoryError as error:
            raise self._map_repository_error(error) from error
        agent = self._agents_for_tasks(session, user_id=user_id, tasks=[task]).get(task.agent_id)
        return self._history_item(
            task,
            agent=agent,
            last_message=(
                self.repository.latest_user_prompt(session, user_id=user_id, task_id=task.id)
                or task.name
            ),
        )

    def delete_task(self, session: Session, *, user_id: int, task_id: int) -> None:
        if not self.repository.soft_delete_if_terminal(session, user_id=user_id, task_id=task_id):
            self._raise_terminal_mutation_error(session, user_id=user_id, task_id=task_id)

    def set_model_override(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        model_override: ModelRef | None,
    ) -> TaskDetailResponse:
        task = self._require_task(session, user_id=user_id, task_id=task_id)
        if task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
            raise TaskServiceError("task_running")
        if not self.repository.set_model_override_if_terminal(
            session,
            user_id=user_id,
            task_id=task_id,
            model_override=model_override,
        ):
            self._raise_terminal_mutation_error(session, user_id=user_id, task_id=task_id)
        return self.get_task(session, user_id=user_id, task_id=task_id)

    def _subtask_responses(
        self,
        session: Session,
        *,
        user_id: int,
        subtasks: Sequence[models.Subtask],
    ) -> list[SubtaskResponse]:
        contexts = self.context_repository.list_for_subtasks(
            session,
            user_id=user_id,
            subtask_ids=[subtask.id for subtask in subtasks],
        )
        contexts_by_subtask: dict[int, list[SubtaskContextBrief]] = defaultdict(list)
        for context in contexts:
            contexts_by_subtask[context.subtask_id].append(
                SubtaskContextBrief.model_validate(context)
            )
        return [
            self._subtask_response(subtask, contexts_by_subtask[subtask.id]) for subtask in subtasks
        ]

    def _subtask_response(
        self,
        subtask: models.Subtask,
        contexts: list[SubtaskContextBrief],
    ) -> SubtaskResponse:
        result = self._project_result(subtask)
        return SubtaskResponse(
            id=subtask.id,
            task_id=subtask.task_id,
            role=subtask.role,
            message_id=subtask.message_id,
            parent_id=subtask.parent_id,
            prompt=subtask.prompt,
            status=subtask.status,
            progress=subtask.progress,
            result=result,
            error_message=subtask.error_message,
            contexts=contexts,
            created_at=subtask.created_at,
            updated_at=subtask.updated_at,
            completed_at=subtask.completed_at,
        )

    def _project_result(self, subtask: models.Subtask) -> dict[str, object] | None:
        if subtask.role != "ASSISTANT":
            # User prompt is the source of truth; do not surface arbitrary
            # persisted JSON for a User row.
            self.history_projector.project_user(subtask)
            return None
        if subtask.status in {"PENDING", "RUNNING"}:
            return None
        raw_result = subtask.result if isinstance(subtask.result, dict) else None
        chain = self.history_projector.project_assistant(subtask)
        if subtask.status in {"FAILED", "CANCELLED"}:
            # Failed rows can contain raw tool output and runtime metadata.
            # The response only retains a validated partial text plus the
            # projector's safe assistant-only history.
            value = raw_result.get("value") if raw_result is not None else None
            if isinstance(value, str) and value:
                return {"value": value, "messages_chain": chain}
            return {"messages_chain": chain}
        if raw_result is None and not chain:
            return None
        projected = (
            {key: deepcopy(value) for key, value in raw_result.items() if key != "messages_chain"}
            if raw_result is not None
            else {}
        )
        projected["messages_chain"] = chain
        return projected

    def _raise_terminal_mutation_error(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
    ) -> None:
        if self.repository.get(session, user_id=user_id, task_id=task_id) is None:
            raise TaskServiceError("task_not_found")
        raise TaskServiceError("task_running")

    def _agents_for_tasks(
        self,
        session: Session,
        *,
        user_id: int,
        tasks: Sequence[models.Task],
    ) -> dict[str, models.Resource]:
        agent_ids = {task.agent_id for task in tasks if task.agent_id is not None}
        return {
            agent.id: agent
            for agent in self.resource_repository.list_visible(
                session,
                user_id=user_id,
                resource_type="agent",
                include_unavailable=True,
                resource_ids=agent_ids,
            )
        }

    def _require_task(self, session: Session, *, user_id: int, task_id: int) -> models.Task:
        task = self.repository.get(session, user_id=user_id, task_id=task_id)
        if task is None:
            raise TaskServiceError("task_not_found")
        return task

    def _history_item(
        self,
        task: models.Task,
        *,
        agent: models.Resource | None,
        last_message: str,
    ) -> TaskHistoryItemResponse:
        return TaskHistoryItemResponse(
            id=task.id,
            name=task.name.strip() or "New task",
            href=f"/chat?task={task.id}",
            agent=TaskAgentResponse(
                id=task.agent_id,
                name=(
                    agent.name
                    if agent is not None
                    else "No agent"
                    if task.agent_id is None
                    else "Unavailable agent"
                ),
                is_available=(
                    task.agent_id is None
                    or (agent is not None and agent.is_active and agent.deleted_at is None)
                ),
            ),
            model_override=self._model_ref(task.model_override_json),
            status=task.status,
            created_at=task.created_at,
            updated_at=task.updated_at,
            last_message=self._excerpt(last_message),
        )

    @staticmethod
    def _model_ref(value: object) -> ModelRef | None:
        if not isinstance(value, dict):
            return None
        try:
            return ModelRef.model_validate(value)
        except ValueError:
            return None

    @staticmethod
    def _last_user_prompt(subtasks: Sequence[models.Subtask], *, fallback: str) -> str:
        for subtask in reversed(subtasks):
            if subtask.role == "USER" and subtask.prompt.strip():
                return subtask.prompt
        return fallback

    @staticmethod
    def _excerpt(value: str, max_length: int = 160) -> str:
        normalized = " ".join(value.split())
        return normalized if len(normalized) <= max_length else normalized[: max_length - 1] + "…"

    @staticmethod
    def _map_repository_error(error: TaskRepositoryError) -> TaskServiceError:
        if error.code == "task_not_found":
            return TaskServiceError("task_not_found")
        return TaskServiceError("task_operation_invalid")
