from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.limits import (
    DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
    DEFAULT_KNOWLEDGE_CHUNK_SIZE,
    DEFAULT_KNOWLEDGE_TOP_K,
    MAX_KNOWLEDGE_CHUNK_OVERLAP,
    MAX_KNOWLEDGE_CHUNK_SIZE,
    MAX_KNOWLEDGE_COLLECTION_NAME_LENGTH,
    MAX_KNOWLEDGE_SCORE_THRESHOLD,
    MAX_KNOWLEDGE_TOP_K,
    MIN_KNOWLEDGE_CHUNK_OVERLAP,
    MIN_KNOWLEDGE_CHUNK_SIZE,
    MIN_KNOWLEDGE_SCORE_THRESHOLD,
    MIN_KNOWLEDGE_TOP_K,
)
from app.schemas.resources import ResourceScope


class KnowledgeCollectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_size: int = Field(
        default=DEFAULT_KNOWLEDGE_CHUNK_SIZE,
        ge=MIN_KNOWLEDGE_CHUNK_SIZE,
        le=MAX_KNOWLEDGE_CHUNK_SIZE,
    )
    chunk_overlap: int = Field(
        default=DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
        ge=MIN_KNOWLEDGE_CHUNK_OVERLAP,
        le=MAX_KNOWLEDGE_CHUNK_OVERLAP,
    )
    top_k: int = Field(
        default=DEFAULT_KNOWLEDGE_TOP_K,
        ge=MIN_KNOWLEDGE_TOP_K,
        le=MAX_KNOWLEDGE_TOP_K,
    )
    score_threshold: float | None = Field(
        default=None,
        ge=MIN_KNOWLEDGE_SCORE_THRESHOLD,
        le=MAX_KNOWLEDGE_SCORE_THRESHOLD,
    )

    @model_validator(mode="after")
    def validate_overlap(self) -> Self:
        if self.chunk_overlap * 2 > self.chunk_size:
            raise ValueError("chunk_overlap must not exceed half of chunk_size")
        return self


class KnowledgeCollectionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(
        min_length=1,
        max_length=MAX_KNOWLEDGE_COLLECTION_NAME_LENGTH,
    )
    config: KnowledgeCollectionConfig = Field(
        default_factory=KnowledgeCollectionConfig
    )


class KnowledgeCollectionPutRequest(KnowledgeCollectionCreateRequest):
    is_active: bool = True


class KnowledgeCollectionResponse(BaseModel):
    id: str
    name: str
    scope: ResourceScope
    can_manage: bool
    is_active: bool
    config: KnowledgeCollectionConfig
    created_at: datetime
    updated_at: datetime


class KnowledgeCollectionListResponse(BaseModel):
    collections: list[KnowledgeCollectionResponse]
