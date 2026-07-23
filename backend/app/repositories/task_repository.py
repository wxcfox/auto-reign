from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db import models
from app.schemas.modeling import ModelRef


class TaskRepositoryError(ValueError):
    """A stable repository-level failure that services can map to transport errors."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class TaskRecentProjection:
    task: models.Task
    last_prompt: str | None


class TaskRepository:
    def create_task(
        self,
        session: Session,
        *,
        user_id: int,
        agent_id: str | None,
        name: str,
        model_override: ModelRef | None,
    ) -> models.Task:
        task = models.Task(
            user_id=user_id,
            agent_id=agent_id,
            name=name,
            status="PENDING",
            model_override_json=self._serialize_model(model_override),
        )
        session.add(task)
        session.flush()
        return task

    def get(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
    ) -> models.Task | None:
        return session.scalar(
            select(models.Task).where(
                models.Task.id == task_id,
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
            )
        )

    def list_recent(
        self,
        session: Session,
        *,
        user_id: int,
        limit: int = 50,
    ) -> list[TaskRecentProjection]:
        if limit <= 0:
            return []
        last_prompt = (
            select(models.Subtask.prompt)
            .where(
                models.Subtask.task_id == models.Task.id,
                models.Subtask.user_id == models.Task.user_id,
                models.Subtask.role == "USER",
            )
            .order_by(models.Subtask.message_id.desc(), models.Subtask.id.desc())
            .limit(1)
            .correlate(models.Task)
            .scalar_subquery()
        )
        rows = session.execute(
            select(models.Task, last_prompt.label("last_prompt"))
            .where(
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
            )
            .order_by(models.Task.created_at.desc(), models.Task.id.desc())
            .limit(limit)
        )
        return [TaskRecentProjection(task=task, last_prompt=prompt) for task, prompt in rows]

    def list_subtasks(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        after_message_id: int | None = None,
    ) -> list[models.Subtask]:
        conditions = [
            models.Subtask.task_id == task_id,
            models.Subtask.user_id == user_id,
            models.Task.user_id == user_id,
            models.Task.is_active.is_(True),
        ]
        if after_message_id is not None:
            conditions.append(models.Subtask.message_id > after_message_id)
        return list(
            session.scalars(
                select(models.Subtask)
                .join(models.Task, models.Task.id == models.Subtask.task_id)
                .where(*conditions)
                .order_by(models.Subtask.message_id, models.Subtask.id)
            )
        )

    def latest_user_prompt(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
    ) -> str | None:
        """Return the latest owner-consistent User prompt in one query."""
        return session.scalar(
            select(models.Subtask.prompt)
            .join(models.Task, models.Task.id == models.Subtask.task_id)
            .where(
                models.Subtask.task_id == task_id,
                models.Subtask.user_id == user_id,
                models.Subtask.role == "USER",
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
            )
            .order_by(models.Subtask.message_id.desc(), models.Subtask.id.desc())
            .limit(1)
        )

    def create_turn(
        self,
        session: Session,
        *,
        task: models.Task,
        prompt: str,
    ) -> tuple[models.Subtask, models.Subtask]:
        previous_message_id = session.scalar(
            select(func.max(models.Subtask.message_id)).where(
                models.Subtask.task_id == task.id,
            )
        )
        user_message_id = (previous_message_id or 0) + 1
        user = models.Subtask(
            user_id=task.user_id,
            task_id=task.id,
            role="USER",
            message_id=user_message_id,
            parent_id=previous_message_id,
            prompt=prompt,
            status="COMPLETED",
            progress=100,
            completed_at=models._now(),
        )
        assistant = models.Subtask(
            user_id=task.user_id,
            task_id=task.id,
            role="ASSISTANT",
            message_id=user_message_id + 1,
            parent_id=user_message_id,
            status="PENDING",
            progress=0,
        )
        task.status = "PENDING"
        task.updated_at = models._now()
        session.add_all([user, assistant])
        session.flush()
        return user, assistant

    def mark_running(
        self,
        session: Session,
        *,
        user_id: int,
        subtask_id: int,
    ) -> models.Subtask:
        assistant, task = self._get_owned_assistant(session, user_id, subtask_id)
        if assistant.status != "PENDING":
            raise TaskRepositoryError("subtask_invalid_status")
        assistant.status = "RUNNING"
        assistant.progress = min(max(assistant.progress, 0), 100)
        assistant.completed_at = None
        task.status = "RUNNING"
        task.updated_at = models._now()
        session.flush()
        return assistant

    def finish_assistant(
        self,
        session: Session,
        *,
        user_id: int,
        subtask_id: int,
        status: str,
        result: dict[str, object] | None,
        error_message: str | None,
    ) -> models.Subtask:
        if status not in {"COMPLETED", "FAILED"}:
            raise TaskRepositoryError("finish_status_invalid")
        assistant, task = self._get_owned_assistant(session, user_id, subtask_id)
        if assistant.status not in {"PENDING", "RUNNING"}:
            raise TaskRepositoryError("subtask_invalid_status")
        completed_at = models._now()
        assistant.status = status
        assistant.progress = 100
        assistant.result = result
        assistant.error_message = error_message
        assistant.completed_at = completed_at
        task.status = status
        task.updated_at = completed_at
        session.flush()
        return assistant

    def cancel_assistant(
        self,
        session: Session,
        *,
        user_id: int,
        subtask_id: int,
        result: dict[str, object] | None,
    ) -> models.Subtask:
        assistant, task = self._get_owned_assistant(session, user_id, subtask_id)
        if assistant.status not in {"PENDING", "RUNNING"}:
            raise TaskRepositoryError("subtask_invalid_status")
        completed_at = models._now()
        assistant.status = "CANCELLED"
        assistant.progress = 100
        assistant.result = result
        assistant.error_message = None
        assistant.completed_at = completed_at
        task.status = "CANCELLED"
        task.updated_at = completed_at
        session.flush()
        return assistant

    def fail_active_assistant(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        subtask_id: int,
        result: dict[str, object] | None,
        error_message: str,
    ) -> bool:
        """Fail an active Assistant without overwriting an ambiguous terminal commit."""

        completed_at = models._now()
        assistant_result = session.execute(
            update(models.Subtask)
            .where(
                models.Subtask.id == subtask_id,
                models.Subtask.task_id == task_id,
                models.Subtask.user_id == user_id,
                models.Subtask.role == "ASSISTANT",
                models.Subtask.status.in_(["PENDING", "RUNNING"]),
            )
            .values(
                status="FAILED",
                progress=100,
                result=result,
                error_message=error_message,
                completed_at=completed_at,
            )
            .execution_options(synchronize_session=False)
        )
        if assistant_result.rowcount != 1:
            return False
        task_result = session.execute(
            update(models.Task)
            .where(
                models.Task.id == task_id,
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
                models.Task.status.in_(["PENDING", "RUNNING"]),
            )
            .values(status="FAILED", updated_at=completed_at)
            .execution_options(synchronize_session=False)
        )
        if task_result.rowcount != 1:
            raise TaskRepositoryError("task_invalid_status")
        session.flush()
        return True

    def reset_failed_assistant(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        subtask_id: int,
    ) -> models.Subtask:
        task = self._require_task(session, user_id=user_id, task_id=task_id)
        subtask = session.scalar(
            select(models.Subtask).where(
                models.Subtask.id == subtask_id,
                models.Subtask.task_id == task.id,
                models.Subtask.user_id == user_id,
            )
        )
        if subtask is None:
            raise TaskRepositoryError("subtask_not_found")
        if subtask.role != "ASSISTANT":
            raise TaskRepositoryError("subtask_not_assistant")
        if subtask.status != "FAILED":
            raise TaskRepositoryError("subtask_not_failed")
        subtask.status = "PENDING"
        subtask.progress = 0
        subtask.result = None
        subtask.error_message = None
        subtask.completed_at = None
        task.status = "PENDING"
        task.updated_at = models._now()
        session.flush()
        return subtask

    def rename(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        name: str,
    ) -> models.Task:
        task = self._require_task(session, user_id=user_id, task_id=task_id)
        task.name = name
        task.updated_at = models._now()
        session.flush()
        return task

    def soft_delete(self, session: Session, *, user_id: int, task_id: int) -> bool:
        task = self.get(session, user_id=user_id, task_id=task_id)
        if task is None:
            return False
        task.is_active = False
        task.updated_at = models._now()
        session.flush()
        return True

    def soft_delete_if_terminal(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
    ) -> bool:
        result = session.execute(
            update(models.Task)
            .where(
                models.Task.id == task_id,
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
                models.Task.status.in_(["COMPLETED", "FAILED", "CANCELLED"]),
            )
            .values(is_active=False, updated_at=models._now())
            .execution_options(synchronize_session="fetch")
        )
        session.flush()
        self._refresh_task(session, task_id=task_id, updated=result.rowcount == 1)
        return result.rowcount == 1

    def set_model_override(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        model_override: ModelRef | None,
    ) -> models.Task:
        task = self._require_task(session, user_id=user_id, task_id=task_id)
        task.model_override_json = self._serialize_model(model_override)
        task.updated_at = models._now()
        session.flush()
        return task

    def set_model_override_if_terminal(
        self,
        session: Session,
        *,
        user_id: int,
        task_id: int,
        model_override: ModelRef | None,
    ) -> bool:
        result = session.execute(
            update(models.Task)
            .where(
                models.Task.id == task_id,
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
                models.Task.status.in_(["COMPLETED", "FAILED", "CANCELLED"]),
            )
            .values(
                model_override_json=self._serialize_model(model_override),
                updated_at=models._now(),
            )
            .execution_options(synchronize_session="fetch")
        )
        session.flush()
        self._refresh_task(session, task_id=task_id, updated=result.rowcount == 1)
        return result.rowcount == 1

    def recover_interrupted(self, session: Session) -> int:
        assistants = list(
            session.scalars(
                select(models.Subtask)
                .join(models.Task, models.Task.id == models.Subtask.task_id)
                .where(
                    models.Subtask.role == "ASSISTANT",
                    models.Subtask.status.in_(["PENDING", "RUNNING"]),
                    models.Subtask.user_id == models.Task.user_id,
                )
            )
        )
        if not assistants:
            return 0
        completed_at = models._now()
        task_ids: set[int] = set()
        for assistant in assistants:
            assistant.status = "FAILED"
            assistant.progress = 100
            assistant.error_message = "generation_interrupted"
            assistant.completed_at = completed_at
            task_ids.add(assistant.task_id)
        for task in session.scalars(select(models.Task).where(models.Task.id.in_(task_ids))):
            task.status = "FAILED"
            task.updated_at = completed_at
        session.flush()
        return len(assistants)

    @staticmethod
    def _serialize_model(model_override: ModelRef | None) -> dict[str, object] | None:
        if model_override is None:
            return None
        return model_override.model_dump(mode="json")

    @staticmethod
    def _refresh_task(session: Session, *, task_id: int, updated: bool) -> None:
        if not updated:
            return
        task = session.get(models.Task, task_id)
        if task is not None:
            session.refresh(task)

    def _require_task(self, session: Session, *, user_id: int, task_id: int) -> models.Task:
        task = self.get(session, user_id=user_id, task_id=task_id)
        if task is None:
            raise TaskRepositoryError("task_not_found")
        return task

    def _get_owned_assistant(
        self,
        session: Session,
        user_id: int,
        subtask_id: int,
    ) -> tuple[models.Subtask, models.Task]:
        row = session.execute(
            select(models.Subtask, models.Task)
            .join(models.Task, models.Task.id == models.Subtask.task_id)
            .where(
                models.Subtask.id == subtask_id,
                models.Subtask.user_id == user_id,
                models.Task.user_id == user_id,
                models.Task.is_active.is_(True),
            )
        ).one_or_none()
        if row is None:
            raise TaskRepositoryError("subtask_not_found")
        subtask, task = row
        if subtask.role != "ASSISTANT":
            raise TaskRepositoryError("subtask_not_assistant")
        return subtask, task
