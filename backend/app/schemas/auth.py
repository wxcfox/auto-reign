from __future__ import annotations

from datetime import datetime

from typing import Literal

from pydantic import BaseModel, Field

from app.core.limits import MAX_USERNAME_LENGTH
from app.core.password_policy import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class AdminPasswordSetupRequest(BaseModel):
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=MAX_USERNAME_LENGTH)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
    )


class UserResponse(BaseModel):
    id: int
    username: str
    display_name: str
    role: Literal["admin", "user"]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
