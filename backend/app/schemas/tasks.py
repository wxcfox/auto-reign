from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.limits import MAX_RESOURCE_NAME_LENGTH
from app.schemas.modeling import ModelRef
from app.schemas.subtask_contexts import SubtaskContextBrief


TaskStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
SubtaskStatus = Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]


class TaskAgentResponse(BaseModel):
    id: str | None
    name: str
    is_available: bool


class TaskHistoryItemResponse(BaseModel):
    id: int
    name: str
    href: str
    agent: TaskAgentResponse
    model_override: ModelRef | None
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    last_message: str


class TaskListResponse(BaseModel):
    tasks: list[TaskHistoryItemResponse]


class SubtaskResponse(BaseModel):
    id: int
    task_id: int
    role: Literal["USER", "ASSISTANT"]
    message_id: int
    parent_id: int | None
    prompt: str
    status: SubtaskStatus
    progress: int
    result: dict[str, object] | None
    error_message: str | None
    contexts: list[SubtaskContextBrief] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class SubtaskListResponse(BaseModel):
    subtasks: list[SubtaskResponse]


class TaskDetailResponse(TaskHistoryItemResponse):
    subtasks: list[SubtaskResponse]


class TaskRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)


class TaskModelPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_override: ModelRef | None
