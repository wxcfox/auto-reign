from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.limits import (
    MAX_AGENT_HOME_FILE_CONTENT_BYTES,
    MAX_ETAG_LENGTH,
    MAX_PROMPT_LENGTH,
    MAX_RESOURCE_NAME_LENGTH,
)
from app.schemas.resources import ResourceScope


class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    workspace_type: Literal["agent_home"]
    initial_agents_md: str = Field(min_length=1, max_length=MAX_PROMPT_LENGTH)


class WorkspaceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=MAX_RESOURCE_NAME_LENGTH)
    config: WorkspaceConfig


class WorkspacePutRequest(WorkspaceCreateRequest):
    is_active: bool = True


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    scope: ResourceScope
    can_manage: bool
    is_active: bool
    config: WorkspaceConfig
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    workspaces: list[WorkspaceResponse]


class WorkspaceFileItem(BaseModel):
    path: str
    name: str
    is_directory: bool
    size_bytes: int | None
    etag: str | None


class WorkspaceFileContent(WorkspaceFileItem):
    is_directory: Literal[False] = False
    content: str


class WorkspaceFileListResponse(BaseModel):
    directory: str
    items: list[WorkspaceFileItem]


class CreateWorkspaceFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str = Field(max_length=MAX_AGENT_HOME_FILE_CONTENT_BYTES)


class WriteWorkspaceFileRequest(CreateWorkspaceFileRequest):
    model_config = ConfigDict(extra="forbid")

    expected_etag: str = Field(min_length=1, max_length=MAX_ETAG_LENGTH)

    @field_validator("expected_etag")
    @classmethod
    def validate_expected_etag_bytes(cls, value: str) -> str:
        try:
            size_bytes = len(value.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("expected_etag must be valid UTF-8") from None
        if size_bytes > MAX_ETAG_LENGTH:
            raise ValueError(
                f"expected_etag exceeds {MAX_ETAG_LENGTH} UTF-8 bytes"
            )
        return value
