from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
