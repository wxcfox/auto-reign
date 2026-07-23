from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.db import models
from app.repositories.task_repository import TaskRepository, TaskRepositoryError
from app.schemas.modeling import ModelRef


@pytest.fixture
def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _add_user(session: Session, user_id: int, username: str) -> models.User:
    user = models.User(
        id=user_id,
        username=username,
        password_hash="not-used",
        display_name=username,
        role="user",
        is_active=True,
        token_version=1,
        settings_json={},
    )
    session.add(user)
    session.flush()
    return user


def _add_task(
    session: Session,
    *,
    user_id: int,
    name: str = "Task",
    status: str = "PENDING",
    is_active: bool = True,
    created_at: datetime | None = None,
) -> models.Task:
    task = models.Task(
        user_id=user_id,
        agent_id=None,
        name=name,
        status=status,
        is_active=is_active,
        created_at=created_at or models._now(),
    )
    session.add(task)
    session.flush()
    return task


def _add_subtask(
    session: Session,
    *,
    task: models.Task,
    user_id: int | None = None,
    role: str,
    message_id: int,
    status: str = "COMPLETED",
    prompt: str = "",
    parent_id: int | None = None,
    progress: int = 100,
    result: dict[str, object] | None = None,
    error_message: str | None = None,
    completed_at: datetime | None = None,
) -> models.Subtask:
    subtask = models.Subtask(
        user_id=task.user_id if user_id is None else user_id,
        task_id=task.id,
        role=role,
        message_id=message_id,
        parent_id=parent_id,
        prompt=prompt,
        status=status,
        progress=progress,
        result=result,
        error_message=error_message,
        completed_at=completed_at,
    )
    session.add(subtask)
    session.flush()
    return subtask


def _assert_error(code: str, operation) -> None:
    with pytest.raises(TaskRepositoryError) as error:
        operation()
    assert error.value.code == code


def test_create_task_persists_serialized_model_override(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    repository = TaskRepository()

    task = repository.create_task(
        db_session,
        user_id=1,
        agent_id="agent-1",
        name="Research",
        model_override=ModelRef(provider="openai", model="gpt-5"),
    )

    assert task.id is not None
    assert task.status == "PENDING"
    assert task.model_override_json == {"provider": "openai", "model": "gpt-5"}


def test_create_turn_uses_unlocked_max_and_continues_message_ids(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    task = _add_task(db_session, user_id=1)
    repository = TaskRepository()

    first_user, first_assistant = repository.create_turn(
        db_session, task=task, prompt="First prompt"
    )
    second_user, second_assistant = repository.create_turn(
        db_session, task=task, prompt="Second prompt"
    )

    assert (first_user.message_id, first_assistant.message_id) == (1, 2)
    assert first_user.parent_id is None
    assert first_assistant.parent_id == first_user.message_id
    assert first_user.role == "USER"
    assert first_user.status == "COMPLETED"
    assert first_user.prompt == "First prompt"
    assert first_user.progress == 100
    assert first_user.completed_at is not None
    assert first_assistant.role == "ASSISTANT"
    assert first_assistant.status == "PENDING"
    assert first_assistant.progress == 0
    assert (second_user.message_id, second_assistant.message_id) == (3, 4)
    assert second_user.parent_id == 2
    assert second_assistant.parent_id == 3
    assert task.status == "PENDING"


def test_create_turn_allows_duplicate_message_ids_in_schema(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    task = _add_task(db_session, user_id=1)
    _add_subtask(db_session, task=task, role="USER", message_id=1, prompt="one")
    _add_subtask(db_session, task=task, role="USER", message_id=1, prompt="duplicate")

    user, assistant = TaskRepository().create_turn(db_session, task=task, prompt="next")

    assert (user.message_id, assistant.message_id) == (2, 3)
    assert (
        len(db_session.scalars(select(models.Subtask).where(models.Subtask.message_id == 1)).all())
        == 2
    )


def test_create_turn_uses_task_scoped_max_for_inconsistent_owner_rows(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1)
    _add_subtask(db_session, task=task, user_id=2, role="USER", message_id=99)

    user, assistant = TaskRepository().create_turn(db_session, task=task, prompt="next")

    assert (user.message_id, assistant.message_id) == (100, 101)
    assert user.parent_id == 99
    assert assistant.parent_id == 100


def test_get_and_lists_are_owner_scoped_and_ordered(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    older = _add_task(
        db_session,
        user_id=1,
        name="older",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    newer = _add_task(
        db_session,
        user_id=1,
        name="newer",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    inactive = _add_task(db_session, user_id=1, name="inactive", is_active=False)
    other = _add_task(db_session, user_id=2, name="other")
    _add_subtask(db_session, task=older, role="USER", message_id=1, prompt="old")
    new_prompt = _add_subtask(db_session, task=newer, role="USER", message_id=1, prompt="new")
    done = _add_subtask(
        db_session,
        task=newer,
        role="ASSISTANT",
        message_id=2,
        result={"text": "done"},
    )
    first = _add_subtask(db_session, task=newer, role="USER", message_id=4, prompt="four")
    second = _add_subtask(db_session, task=newer, role="USER", message_id=4, prompt="four-again")

    repository = TaskRepository()

    assert repository.get(db_session, user_id=1, task_id=newer.id) is newer
    assert repository.get(db_session, user_id=2, task_id=newer.id) is None
    assert repository.get(db_session, user_id=1, task_id=inactive.id) is None
    recent = repository.list_recent(db_session, user_id=1)
    assert [(item.task.id, item.last_prompt) for item in recent] == [
        (newer.id, "four-again"),
        (older.id, "old"),
    ]
    assert repository.list_recent(db_session, user_id=2)[0].task.id == other.id
    assert [item.task.id for item in repository.list_recent(db_session, user_id=1, limit=1)] == [
        newer.id
    ]
    assert repository.list_recent(db_session, user_id=1, limit=0) == []
    assert repository.list_recent(db_session, user_id=1, limit=-1) == []
    assert [
        item.id for item in repository.list_subtasks(db_session, user_id=1, task_id=newer.id)
    ] == [new_prompt.id, done.id, first.id, second.id]
    assert [
        item.id
        for item in repository.list_subtasks(
            db_session, user_id=1, task_id=newer.id, after_message_id=2
        )
    ] == [first.id, second.id]
    assert repository.list_subtasks(db_session, user_id=2, task_id=newer.id) == []


def test_mismatched_subtask_owner_is_excluded_from_owner_scoped_operations(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1, status="FAILED")
    mismatched_prompt = _add_subtask(
        db_session,
        task=task,
        user_id=2,
        role="USER",
        message_id=1,
        prompt="do not expose",
    )
    mismatched_assistant = _add_subtask(
        db_session,
        task=task,
        user_id=2,
        role="ASSISTANT",
        message_id=2,
        status="FAILED",
        result={"private": "bob"},
    )
    repository = TaskRepository()

    assert repository.list_recent(db_session, user_id=1)[0].last_prompt is None
    assert repository.list_subtasks(db_session, user_id=1, task_id=task.id) == []
    _assert_error(
        "subtask_not_found",
        lambda: repository.mark_running(db_session, user_id=1, subtask_id=mismatched_assistant.id),
    )
    _assert_error(
        "subtask_not_found",
        lambda: repository.reset_failed_assistant(
            db_session,
            user_id=1,
            task_id=task.id,
            subtask_id=mismatched_assistant.id,
        ),
    )
    assert mismatched_prompt.prompt == "do not expose"


def test_reset_failed_assistant_reuses_exact_row_and_checks_target(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1, status="FAILED")
    failed = _add_subtask(
        db_session,
        task=task,
        role="ASSISTANT",
        message_id=2,
        status="FAILED",
        progress=100,
        result={"partial": "answer"},
        error_message="boom",
        completed_at=models._now(),
    )
    user = _add_subtask(db_session, task=task, role="USER", message_id=1)
    repository = TaskRepository()

    reset = repository.reset_failed_assistant(
        db_session, user_id=1, task_id=task.id, subtask_id=failed.id
    )

    assert reset is failed
    assert reset.status == "PENDING"
    assert reset.progress == 0
    assert reset.result is None
    assert reset.error_message is None
    assert reset.completed_at is None
    assert task.status == "PENDING"
    assert (
        len(
            db_session.scalars(
                select(models.Subtask).where(models.Subtask.task_id == task.id)
            ).all()
        )
        == 2
    )
    _assert_error(
        "task_not_found",
        lambda: repository.reset_failed_assistant(
            db_session, user_id=2, task_id=task.id, subtask_id=failed.id
        ),
    )
    _assert_error(
        "subtask_not_assistant",
        lambda: repository.reset_failed_assistant(
            db_session, user_id=1, task_id=task.id, subtask_id=user.id
        ),
    )
    failed.status = "COMPLETED"
    _assert_error(
        "subtask_not_failed",
        lambda: repository.reset_failed_assistant(
            db_session, user_id=1, task_id=task.id, subtask_id=failed.id
        ),
    )


def test_mark_running_validates_owner_role_and_status(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1)
    pending = _add_subtask(
        db_session, task=task, role="ASSISTANT", message_id=2, status="PENDING", progress=0
    )
    user = _add_subtask(db_session, task=task, role="USER", message_id=1)
    repository = TaskRepository()

    assert repository.mark_running(db_session, user_id=1, subtask_id=pending.id) is pending
    assert (pending.status, pending.progress, pending.completed_at, task.status) == (
        "RUNNING",
        0,
        None,
        "RUNNING",
    )
    _assert_error(
        "subtask_invalid_status",
        lambda: repository.mark_running(db_session, user_id=1, subtask_id=pending.id),
    )
    _assert_error(
        "subtask_not_assistant",
        lambda: repository.mark_running(db_session, user_id=1, subtask_id=user.id),
    )
    _assert_error(
        "subtask_not_found",
        lambda: repository.mark_running(db_session, user_id=2, subtask_id=pending.id),
    )


def test_finish_assistant_handles_completed_and_failed_outcomes(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1)
    pending = _add_subtask(
        db_session, task=task, role="ASSISTANT", message_id=2, status="PENDING", progress=0
    )
    failed = _add_subtask(
        db_session, task=task, role="ASSISTANT", message_id=4, status="RUNNING", progress=50
    )
    repository = TaskRepository()

    assert (
        repository.finish_assistant(
            db_session,
            user_id=1,
            subtask_id=pending.id,
            status="COMPLETED",
            result={"answer": "done"},
            error_message=None,
        )
        is pending
    )
    assert (
        pending.status,
        pending.progress,
        pending.result,
        pending.completed_at,
        task.status,
    ) == ("COMPLETED", 100, {"answer": "done"}, pending.completed_at, "COMPLETED")
    assert pending.completed_at is not None
    assert (
        repository.finish_assistant(
            db_session,
            user_id=1,
            subtask_id=failed.id,
            status="FAILED",
            result={"partial": "answer"},
            error_message="provider_error",
        )
        is failed
    )
    assert (failed.status, failed.progress, failed.result, failed.error_message) == (
        "FAILED",
        100,
        {"partial": "answer"},
        "provider_error",
    )
    assert task.status == "FAILED"
    assert failed.completed_at is not None
    _assert_error(
        "finish_status_invalid",
        lambda: repository.finish_assistant(
            db_session,
            user_id=1,
            subtask_id=pending.id,
            status="PENDING",
            result=None,
            error_message=None,
        ),
    )
    _assert_error(
        "subtask_not_found",
        lambda: repository.finish_assistant(
            db_session,
            user_id=2,
            subtask_id=pending.id,
            status="FAILED",
            result=None,
            error_message="no access",
        ),
    )


def test_cancel_assistant_validates_owner_and_status(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1)
    cancellable = _add_subtask(
        db_session, task=task, role="ASSISTANT", message_id=4, status="RUNNING", progress=40
    )
    repository = TaskRepository()
    repository.cancel_assistant(
        db_session, user_id=1, subtask_id=cancellable.id, result={"partial": "x"}
    )
    assert (cancellable.status, cancellable.progress, cancellable.error_message, task.status) == (
        "CANCELLED",
        100,
        None,
        "CANCELLED",
    )
    _assert_error(
        "subtask_invalid_status",
        lambda: repository.cancel_assistant(
            db_session, user_id=1, subtask_id=cancellable.id, result=None
        ),
    )
    _assert_error(
        "subtask_not_found",
        lambda: repository.cancel_assistant(
            db_session, user_id=2, subtask_id=cancellable.id, result=None
        ),
    )


def test_task_mutations_validate_active_owner(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task = _add_task(db_session, user_id=1)
    repository = TaskRepository()

    updated_before = task.updated_at
    repository.rename(db_session, user_id=1, task_id=task.id, name="Renamed")
    assert task.name == "Renamed"
    assert task.updated_at >= updated_before
    repository.set_model_override(
        db_session,
        user_id=1,
        task_id=task.id,
        model_override=ModelRef(provider="qwen", model="qwen3"),
    )
    assert task.model_override_json == {"provider": "qwen", "model": "qwen3"}
    repository.set_model_override(db_session, user_id=1, task_id=task.id, model_override=None)
    assert task.model_override_json is None
    _assert_error(
        "task_not_found",
        lambda: repository.set_model_override(
            db_session, user_id=2, task_id=task.id, model_override=None
        ),
    )
    _assert_error(
        "task_not_found",
        lambda: repository.rename(db_session, user_id=2, task_id=task.id, name="no access"),
    )
    assert repository.soft_delete(db_session, user_id=2, task_id=task.id) is False
    assert repository.soft_delete(db_session, user_id=1, task_id=task.id) is True
    assert task.is_active is False
    _assert_error(
        "task_not_found",
        lambda: repository.rename(db_session, user_id=1, task_id=task.id, name="nope"),
    )


def test_terminal_conditional_mutations_and_latest_prompt(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    terminal = _add_task(db_session, user_id=1, status="COMPLETED")
    running = _add_task(db_session, user_id=1, status="RUNNING")
    unrelated = _add_task(db_session, user_id=1, status="COMPLETED")
    _add_subtask(db_session, task=terminal, role="USER", message_id=1, prompt="first")
    _add_subtask(db_session, task=terminal, role="USER", message_id=2, prompt="latest")
    _add_subtask(db_session, task=terminal, user_id=2, role="USER", message_id=3, prompt="other")
    repository = TaskRepository()

    assert repository.latest_user_prompt(db_session, user_id=1, task_id=terminal.id) == "latest"
    assert repository.set_model_override_if_terminal(
        db_session,
        user_id=1,
        task_id=terminal.id,
        model_override=ModelRef(provider="openai", model="gpt-5"),
    )
    assert terminal.model_override_json == {"provider": "openai", "model": "gpt-5"}
    assert not inspect(unrelated).expired
    assert not repository.set_model_override_if_terminal(
        db_session, user_id=1, task_id=running.id, model_override=None
    )
    assert not repository.soft_delete_if_terminal(db_session, user_id=1, task_id=running.id)
    assert running.is_active is True
    assert repository.soft_delete_if_terminal(db_session, user_id=1, task_id=terminal.id)
    assert terminal.is_active is False
    assert repository.latest_user_prompt(db_session, user_id=1, task_id=terminal.id) is None


def test_inactive_task_rejects_subtask_mutations(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    task = _add_task(db_session, user_id=1, is_active=False)
    assistant = _add_subtask(
        db_session, task=task, role="ASSISTANT", message_id=2, status="PENDING", progress=0
    )
    repository = TaskRepository()

    _assert_error(
        "subtask_not_found",
        lambda: repository.mark_running(db_session, user_id=1, subtask_id=assistant.id),
    )
    _assert_error(
        "subtask_not_found",
        lambda: repository.finish_assistant(
            db_session,
            user_id=1,
            subtask_id=assistant.id,
            status="FAILED",
            result=None,
            error_message="no access",
        ),
    )
    _assert_error(
        "subtask_not_found",
        lambda: repository.cancel_assistant(
            db_session, user_id=1, subtask_id=assistant.id, result=None
        ),
    )
    _assert_error(
        "task_not_found",
        lambda: repository.reset_failed_assistant(
            db_session, user_id=1, task_id=task.id, subtask_id=assistant.id
        ),
    )


def test_recover_interrupted_marks_selected_assistants_and_tasks_failed(
    db_session: Session,
) -> None:
    _add_user(db_session, 1, "alice")
    _add_user(db_session, 2, "bob")
    task_one = _add_task(db_session, user_id=1, status="RUNNING")
    inactive_task = _add_task(db_session, user_id=1, status="RUNNING", is_active=False)
    interrupted_pending = _add_subtask(
        db_session,
        task=task_one,
        role="ASSISTANT",
        message_id=2,
        status="PENDING",
        progress=0,
        result={"partial": "keep"},
    )
    interrupted_running = _add_subtask(
        db_session, task=task_one, role="ASSISTANT", message_id=4, status="RUNNING", progress=50
    )
    terminal = _add_subtask(
        db_session,
        task=task_one,
        role="ASSISTANT",
        message_id=6,
        status="COMPLETED",
        result={"answer": "done"},
    )
    inactive_assistant = _add_subtask(
        db_session, task=inactive_task, role="ASSISTANT", message_id=2, status="PENDING", progress=0
    )
    mismatched_assistant = _add_subtask(
        db_session,
        task=task_one,
        user_id=2,
        role="ASSISTANT",
        message_id=8,
        status="PENDING",
        progress=0,
    )
    user_pending = _add_subtask(
        db_session, task=task_one, role="USER", message_id=1, status="PENDING", progress=0
    )

    recovered = TaskRepository().recover_interrupted(db_session)

    assert recovered == 3
    for subtask in (interrupted_pending, interrupted_running, inactive_assistant):
        assert (subtask.status, subtask.progress, subtask.error_message) == (
            "FAILED",
            100,
            "generation_interrupted",
        )
        assert subtask.completed_at is not None
    assert interrupted_pending.result == {"partial": "keep"}
    assert task_one.status == "FAILED"
    assert inactive_task.status == "FAILED"
    assert terminal.status == "COMPLETED"
    assert user_pending.status == "PENDING"
    assert mismatched_assistant.status == "PENDING"
    assert TaskRepository().recover_interrupted(db_session) == 0
    assert terminal.result == {"answer": "done"}


def test_mutations_are_transaction_neutral(db_session: Session) -> None:
    _add_user(db_session, 1, "alice")
    task = _add_task(db_session, user_id=1)
    assistant = _add_subtask(
        db_session, task=task, role="ASSISTANT", message_id=2, status="PENDING", progress=0
    )
    db_session.commit()

    TaskRepository().mark_running(db_session, user_id=1, subtask_id=assistant.id)
    db_session.rollback()
    db_session.expire_all()

    persisted = db_session.get(models.Subtask, assistant.id)
    persisted_task = db_session.get(models.Task, task.id)
    assert persisted is not None
    assert persisted_task is not None
    assert (persisted.status, persisted_task.status) == ("PENDING", "PENDING")
