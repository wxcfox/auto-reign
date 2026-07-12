from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ConversationKind = Literal["chat", "interview", "learning"]
ConversationRole = Literal["assistant", "system", "user"]


class ConversationMessageResponse(BaseModel):
    id: str
    role: ConversationRole
    message_type: str
    content: str
    created_at: datetime
    metadata: dict[str, object] = Field(default_factory=dict)


class ConversationHistoryItemResponse(BaseModel):
    id: str
    kind: ConversationKind
    title: str
    href: str
    started_at: datetime
    updated_at: datetime
    last_message: str


class ConversationListResponse(BaseModel):
    conversations: list[ConversationHistoryItemResponse]


class ConversationDetailResponse(ConversationHistoryItemResponse):
    messages: list[ConversationMessageResponse]


class ConversationRenameRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=120)


class ConversationDeleteResponse(BaseModel):
    id: str
    status: Literal["deleted"]
