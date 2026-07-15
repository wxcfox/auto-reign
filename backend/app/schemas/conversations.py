from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.limits import (
    MAX_ATTACHMENT_ID_LENGTH,
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_CONVERSATION_TITLE_LENGTH,
)
from app.schemas.attachments import AttachmentResponse
from app.schemas.modeling import ModelRef


class ConversationMessageResponse(BaseModel):
    id: str
    role: Literal["assistant", "user"]
    status: Literal["pending", "streaming", "completed", "failed"]
    content: str
    provider: str | None
    model: str | None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)
    attachments: list[AttachmentResponse] = Field(default_factory=list)


class ConversationAgentResponse(BaseModel):
    id: str
    name: str
    is_available: bool


class ConversationHistoryItemResponse(BaseModel):
    id: str
    title: str
    href: str
    agent: ConversationAgentResponse
    model_override: ModelRef | None
    status: Literal["idle", "generating"]
    started_at: datetime
    updated_at: datetime
    last_message: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationHistoryItemResponse]


class ConversationDetailResponse(ConversationHistoryItemResponse):
    messages: list[ConversationMessageResponse]


class ConversationSendRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    conversation_id: str | None = None
    agent_id: str | None = None
    model_override: ModelRef | None = None
    attachment_ids: list[str] = Field(
        default_factory=list,
        max_length=MAX_ATTACHMENTS_PER_MESSAGE,
    )

    @field_validator("attachment_ids")
    @classmethod
    def validate_attachment_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("attachment ids must be unique")
        if any(
            not attachment_id or len(attachment_id) > MAX_ATTACHMENT_ID_LENGTH
            for attachment_id in value
        ):
            raise ValueError(
                "attachment ids must be non-empty and at most "
                f"{MAX_ATTACHMENT_ID_LENGTH} characters"
            )
        return value


class ConversationModelPutRequest(BaseModel):
    model_override: ModelRef | None


class ConversationRenameRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=MAX_CONVERSATION_TITLE_LENGTH)


class ConversationDeleteResponse(BaseModel):
    id: str
    status: Literal["deleted"]
