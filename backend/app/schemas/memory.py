from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

MemoryKind = Literal["weakness", "interview_history", "learning_profile"]


class MemoryFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kind: MemoryKind
    file_path: str
    summary_hash: str
    last_indexed_at: datetime | None = None
    updated_at: datetime


class MemoryFileContent(BaseModel):
    kind: MemoryKind
    content: str
    updated_at: datetime | None = None


class MemoryResponse(BaseModel):
    files: dict[MemoryKind, MemoryFileContent]
