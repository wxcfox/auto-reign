from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.chat import ChatSendRequest
from app.schemas.modeling import ModelRef
from app.schemas.tasks import SubtaskResponse
from app.services.chat_blocks import copy_chat_block, validate_chat_block_id


PositiveId = Annotated[int, Field(gt=0, strict=True)]
GenerationId = Annotated[str, Field(min_length=1, max_length=64)]


class SocketPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TaskJoinPayload(SocketPayload):
    task_id: PositiveId
    after_message_id: int | None = Field(default=None, ge=0, strict=True)


class TaskLeavePayload(SocketPayload):
    task_id: PositiveId


class ChatSendPayload(SocketPayload):
    task_id: PositiveId | None = None
    message: str = Field(min_length=1, max_length=20_000)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_override: ModelRef | None = None
    context_ids: list[PositiveId] = Field(default_factory=list, max_length=10)

    @field_validator("context_ids")
    @classmethod
    def validate_context_ids(cls, value: list[int]) -> list[int]:
        if len(value) != len(set(value)):
            raise ValueError("context IDs must be unique")
        return value

    def to_request(self) -> ChatSendRequest:
        return ChatSendRequest.model_validate(self.model_dump())


class ChatCancelPayload(SocketPayload):
    task_id: PositiveId


class ChatRetryPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId


class ActiveStreamSnapshotPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    offset: int = Field(ge=0, strict=True)
    cached_content: str
    blocks: list[dict[str, object]]
    started_at: datetime
    last_activity_at: datetime
    status_updated: dict[str, object] | None = None

    @field_validator("blocks")
    @classmethod
    def validate_blocks(
        cls, value: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        return [dict(copy_chat_block(block)) for block in value]


class TaskJoinAck(SocketPayload):
    task_id: PositiveId
    subtasks: list[SubtaskResponse]
    streaming: ActiveStreamSnapshotPayload | None = None


class TaskLeaveAck(SocketPayload):
    task_id: PositiveId


class ChatSendAck(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    message_id: PositiveId


class ChatCancelAck(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId | None = None
    accepted: bool


class ChatRetryAck(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId


class SocketErrorDetail(SocketPayload):
    code: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class SocketErrorAck(SocketPayload):
    error: SocketErrorDetail


class ChatStartPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    status: Literal["RUNNING"] = "RUNNING"


class ChatChunkPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    block_id: str
    block_offset: int = Field(ge=0)
    offset: int = Field(ge=0)
    content: str = Field(min_length=1)

    @field_validator("block_id")
    @classmethod
    def validate_block_id(cls, value: str) -> str:
        return validate_chat_block_id(value)


class ChatBlockCreatedPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    block: dict[str, object]

    @field_validator("block")
    @classmethod
    def validate_block(cls, value: dict[str, object]) -> dict[str, object]:
        return dict(copy_chat_block(value))


class ChatBlockUpdatedPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    block_id: str
    content: str | None = None
    tool_input: dict[str, object] | None = None
    tool_output: object | None = None
    status: Literal[
        "generating_arguments", "pending", "streaming", "done", "error"
    ] | None = None

    @field_validator("block_id")
    @classmethod
    def validate_block_id(cls, value: str) -> str:
        return validate_chat_block_id(value)


class ChatDonePayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    result: dict[str, object]


class ChatErrorPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    code: str = Field(min_length=1, max_length=128)
    result: dict[str, object] | None = None


class ChatCancelledPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    result: dict[str, object] | None = None


class ChatStatusUpdatedPayload(SocketPayload):
    task_id: PositiveId
    subtask_id: PositiveId
    generation_id: GenerationId
    status: dict[str, object]


class TaskAgentBriefPayload(SocketPayload):
    id: str | None
    name: str
    is_available: bool


class TaskBriefPayload(SocketPayload):
    id: PositiveId
    name: str
    href: str
    status: Literal["PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"]
    agent: TaskAgentBriefPayload
    model_override: ModelRef | None
    created_at: datetime
    updated_at: datetime


class TaskCreatedPayload(SocketPayload):
    task: TaskBriefPayload


class TaskStatusPayload(SocketPayload):
    task: TaskBriefPayload
