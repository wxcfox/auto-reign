from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.limits import (
    MAX_DOCUMENTS_PER_SCOPE,
    MAX_KNOWLEDGE_SCOPES,
    MAX_PROMPT_LENGTH,
    MAX_RESOURCE_NAME_LENGTH,
)
from app.schemas.modeling import ModelRef
from app.schemas.resources import ResourceId, ResourceScope


class KnowledgeScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    collection_id: ResourceId
    document_ids: list[ResourceId] | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_DOCUMENTS_PER_SCOPE,
    )

    @field_validator("document_ids")
    @classmethod
    def validate_document_ids(cls, value: list[str] | None) -> list[str] | None:
        if value == []:
            raise ValueError("document_ids must be null or a non-empty list")
        if value is not None and len(set(value)) != len(value):
            raise ValueError("document_ids must be unique")
        return value


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    system_prompt: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)
    default_model: ModelRef | None = None
    home_workspace_id: ResourceId | None = None
    knowledge_scopes: list[KnowledgeScope] = Field(
        default_factory=list,
        max_length=MAX_KNOWLEDGE_SCOPES,
    )

    @model_validator(mode="after")
    def collection_ids_are_unique(self) -> "AgentConfig":
        collection_ids = [item.collection_id for item in self.knowledge_scopes]
        if len(collection_ids) != len(set(collection_ids)):
            raise ValueError("collection_id may appear only once")
        return self


class AgentCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)
    config: AgentConfig


class AgentPutRequest(AgentCreateRequest):
    is_active: bool = True


class AgentResponse(BaseModel):
    id: str
    name: str
    scope: ResourceScope
    can_manage: bool
    is_active: bool
    config: AgentConfig
    created_at: datetime
    updated_at: datetime


class AgentListResponse(BaseModel):
    agents: list[AgentResponse]
