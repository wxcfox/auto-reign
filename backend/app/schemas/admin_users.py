from pydantic import BaseModel, Field

from app.core.limits import (
    MAX_DISPLAY_NAME_LENGTH,
    MAX_USERNAME_LENGTH,
    MIN_USERNAME_LENGTH,
)
from app.core.password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from app.schemas.auth import UserResponse


class AdminUserCreateRequest(BaseModel):
    username: str = Field(
        min_length=MIN_USERNAME_LENGTH,
        max_length=MAX_USERNAME_LENGTH,
        pattern=r"^[a-zA-Z0-9_.-]+$",
    )
    display_name: str = Field(default="", max_length=MAX_DISPLAY_NAME_LENGTH)
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )


class AdminUserStatusRequest(BaseModel):
    is_active: bool


class AdminUserPasswordResetRequest(BaseModel):
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )


class AdminUserListResponse(BaseModel):
    users: list[UserResponse]
