from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.modeling import ModelRef


class ChatSendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: int | None = Field(default=None, gt=0)
    message: str = Field(min_length=1, max_length=20_000)
    agent_id: str | None = Field(default=None, min_length=1, max_length=36)
    model_override: ModelRef | None = None
    context_ids: list[int] = Field(default_factory=list, max_length=10)

    @field_validator("context_ids")
    @classmethod
    def validate_context_ids(cls, value: list[int]) -> list[int]:
        if any(context_id <= 0 for context_id in value):
            raise ValueError("context IDs must be positive")
        if len(value) != len(set(value)):
            raise ValueError("context IDs must be unique")
        return value
