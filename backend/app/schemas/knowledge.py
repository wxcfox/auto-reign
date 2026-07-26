from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.limits import MAX_RESOURCE_NAME_LENGTH


class KnowledgeDocumentRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    collection_id: str
    name: str
    mime_type: str
    size_bytes: int
    status: Literal["uploaded", "queued", "processing", "ready", "failed"]
    index_generation: int
    error_code: str | None
    error_message: str | None
    is_active: bool
    indexed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class KnowledgeDocumentListResponse(BaseModel):
    documents: list[KnowledgeDocumentResponse]


class KnowledgeDocumentContentResponse(BaseModel):
    document_id: str
    content: str
