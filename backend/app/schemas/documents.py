from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AnalysisStatus = Literal["pending", "completed", "failed"]
FileType = Literal["markdown", "txt"]
IndexStatus = Literal["pending", "completed", "failed"]


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    collection: str
    source_filename: str
    file_path: str
    file_type: FileType
    title: str
    summary: str
    tags: list[str] = Field(default_factory=list)
    knowledge_points: list[str] = Field(default_factory=list)
    weakness_candidates: list[str] = Field(default_factory=list)
    analysis_status: AnalysisStatus
    index_status: IndexStatus
    created_at: datetime
    updated_at: datetime


class DocumentUpdate(BaseModel):
    title: str | None = None
    summary: str | None = None
    tags: list[str] | None = None
    knowledge_points: list[str] | None = None
    weakness_candidates: list[str] | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentResponse]
