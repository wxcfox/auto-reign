from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.db import models


class AttachmentDraftDTO(BaseModel):
    model_config = ConfigDict(frozen=True, from_attributes=True)

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    object_key: str
    parsed_object_key: str | None
    message_id: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, attachment: models.Attachment) -> AttachmentDraftDTO:
        return cls(
            id=attachment.id,
            filename=attachment.original_filename,
            mime_type=attachment.mime_type,
            size_bytes=attachment.size_bytes,
            object_key=attachment.object_key,
            parsed_object_key=attachment.parsed_object_key,
            message_id=attachment.message_id,
            created_at=attachment.created_at,
        )


class AttachmentContentDTO(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    message_id: str | None
    content: bytes


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    message_id: str | None
    created_at: datetime


class AttachmentDraftListResponse(BaseModel):
    items: tuple[AttachmentResponse, ...]
