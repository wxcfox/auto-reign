from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SubtaskContextType = Literal["attachment", "knowledge_base", "selected_documents"]
SubtaskContextStatus = Literal[
    "pending",
    "uploading",
    "parsing",
    "ready",
    "empty",
    "failed",
]


class SubtaskContextBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    context_type: SubtaskContextType
    name: str
    status: SubtaskContextStatus
    mime_type: str | None = None
    file_extension: str | None = None
    file_size: int | None = None
    text_length: int
    type_data: dict[str, object] = Field(default_factory=dict)

    @field_validator("type_data", mode="before")
    @classmethod
    def copy_type_data(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("type_data must be an object")
        return deepcopy(value)


class SubtaskContextBriefList(BaseModel):
    items: tuple[SubtaskContextBrief, ...]


class SubtaskContextContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: str
    mime_type: str
    file_size: int
    content: bytes
