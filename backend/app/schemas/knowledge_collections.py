from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.limits import (
    DEFAULT_KNOWLEDGE_CHUNK_OVERLAP,
    DEFAULT_KNOWLEDGE_CHUNK_SIZE,
    DEFAULT_KNOWLEDGE_KEYWORD_WEIGHT,
    DEFAULT_KNOWLEDGE_SCORE_THRESHOLD,
    DEFAULT_KNOWLEDGE_TOP_K,
    DEFAULT_KNOWLEDGE_VECTOR_WEIGHT,
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


class KnowledgeCollectionConfigInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retriever_type: Literal["elasticsearch", "qdrant"] = "elasticsearch"
    retrieval_mode: Literal["vector", "keyword", "hybrid"] = "vector"
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
    score_threshold: float = Field(
        default=DEFAULT_KNOWLEDGE_SCORE_THRESHOLD,
        ge=MIN_KNOWLEDGE_SCORE_THRESHOLD,
        le=MAX_KNOWLEDGE_SCORE_THRESHOLD,
    )
    vector_weight: float = Field(
        default=DEFAULT_KNOWLEDGE_VECTOR_WEIGHT,
        ge=0.0,
        le=1.0,
    )
    keyword_weight: float = Field(
        default=DEFAULT_KNOWLEDGE_KEYWORD_WEIGHT,
        ge=0.0,
        le=1.0,
    )

    @model_validator(mode="after")
    def validate_common_constraints(self) -> Self:
        if self.chunk_overlap * 2 > self.chunk_size:
            raise ValueError("chunk_overlap must not exceed half of chunk_size")
        if self.vector_weight + self.keyword_weight <= 0:
            raise ValueError("vector_weight and keyword_weight must have a positive sum")
        return self


class KnowledgeCollectionConfig(KnowledgeCollectionConfigInput):
    @model_validator(mode="after")
    def validate_retrieval_capability(self) -> Self:
        if self.retriever_type == "qdrant" and self.retrieval_mode != "vector":
            raise ValueError(
                "Qdrant supports only vector retrieval; use Elasticsearch for "
                "keyword or hybrid retrieval"
            )
        return self


class KnowledgeCollectionPutConfig(KnowledgeCollectionConfigInput):
    """Full update input whose Retriever capability is validated after immutability."""


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
    config: KnowledgeCollectionPutConfig = Field(
        default_factory=KnowledgeCollectionPutConfig
    )
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
