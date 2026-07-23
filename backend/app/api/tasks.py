from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.dependencies import SessionDep, get_current_user
from app.core.errors import not_found
from app.db import models
from app.repositories.task_repository import TaskRepository
from app.schemas.modeling import ModelRef
from app.schemas.tasks import (
    TaskDetailResponse,
    TaskHistoryItemResponse,
    TaskListResponse,
    TaskModelPutRequest,
    TaskRenameRequest,
)
from app.services.task_service import TaskService, TaskServiceError
from app.services.agent_service import AgentService


router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _service_error(error: TaskServiceError) -> HTTPException:
    mapping = {
        "task_not_found": (404, "Task was not found."),
        "task_running": (409, "Task has an active generation."),
        "model_unavailable": (503, "The selected model is unavailable."),
        "agent_unavailable": (409, "Agent is unavailable."),
        "task_name_invalid": (400, "Task name is invalid."),
        "task_operation_invalid": (400, "Task operation is invalid."),
    }
    status_code, message = mapping.get(error.code, (500, "Task operation failed."))
    code = error.code if error.code in mapping else "task_error"
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _task_service(request: Request) -> TaskService:
    service = getattr(request.app.state, "task_service", None)
    return service if service is not None else TaskService()


def _validate_model_override(
    request: Request,
    session: SessionDep,
    *,
    user_id: int,
    task_id: int,
    model_override: ModelRef | None,
) -> None:
    task = TaskRepository().get(session, user_id=user_id, task_id=task_id)
    if task is None:
        raise not_found("task_not_found", "Task was not found.")
    if task.status not in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise _service_error(TaskServiceError("task_running"))
    agents = AgentService(settings=request.app.state.settings)
    agent = (
        agents.resolve_for_turn(session, user_id=user_id, agent_id=task.agent_id)
        if task.agent_id is not None
        else None
    )
    agents.resolve_model(agent=agent, conversation_override=model_override)


@router.get("", response_model=TaskListResponse)
def list_tasks(
    request: Request,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> TaskListResponse:
    return TaskListResponse(
        tasks=_task_service(request).list_tasks(session, user_id=current_user.id)
    )


@router.get("/{task_id}", response_model=TaskDetailResponse)
def get_task(
    task_id: int,
    request: Request,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> TaskDetailResponse:
    try:
        return _task_service(request).get_task(session, user_id=current_user.id, task_id=task_id)
    except TaskServiceError as error:
        raise _service_error(error) from error


@router.patch("/{task_id}", response_model=TaskHistoryItemResponse)
def rename_task(
    task_id: int,
    payload: TaskRenameRequest,
    request: Request,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> TaskHistoryItemResponse:
    try:
        return _task_service(request).rename_task(
            session, user_id=current_user.id, task_id=task_id, name=payload.name
        )
    except TaskServiceError as error:
        raise _service_error(error) from error


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(
    task_id: int,
    request: Request,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> Response:
    try:
        _task_service(request).delete_task(session, user_id=current_user.id, task_id=task_id)
    except TaskServiceError as error:
        raise _service_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{task_id}/model", response_model=TaskDetailResponse)
def put_task_model(
    task_id: int,
    payload: TaskModelPutRequest,
    request: Request,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
) -> TaskDetailResponse:
    try:
        _validate_model_override(
            request,
            session,
            user_id=current_user.id,
            task_id=task_id,
            model_override=payload.model_override,
        )
        return _task_service(request).set_model_override(
            session,
            user_id=current_user.id,
            task_id=task_id,
            model_override=payload.model_override,
        )
    except TaskServiceError as error:
        raise _service_error(error) from error
