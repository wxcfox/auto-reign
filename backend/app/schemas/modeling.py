from pydantic import BaseModel, ConfigDict, Field


class ModelRef(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        frozen=True,
    )

    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=160)
