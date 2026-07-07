from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_session
from app.core.auth import create_access_token
from app.core.passwords import hash_password, verify_password
from app.db import models
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/api/auth")


def _token_response(user: models.User) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(
            user.username,
            user.id,
            user.token_version,
        ),
        user=user,
    )


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={
            "code": "invalid_credentials",
            "message": "Username or password is incorrect.",
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/register", response_model=TokenResponse)
def register(
    request: RegisterRequest,
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = models.User(
        username=request.username,
        password_hash=hash_password(request.password),
        display_name=request.display_name or request.username,
        settings_json={
            "schema_version": 1,
            "language": "zh-CN",
            "active_collection": "",
        },
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "username_taken",
                "message": "Username is already registered.",
            },
        ) from exc

    user.settings_json = {
        **user.settings_json,
        "active_collection": f"auto_reign_user_{user.id}",
    }
    session.flush()
    return _token_response(user)


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    session: Session = Depends(get_session),
) -> TokenResponse:
    user = session.scalar(
        select(models.User).where(models.User.username == request.username)
    )
    if user is None or not user.is_active:
        raise _invalid_credentials()
    if not verify_password(request.password, user.password_hash):
        raise _invalid_credentials()
    return _token_response(user)


@router.get("/me", response_model=UserResponse)
def me(current_user: models.User = Depends(get_current_user)) -> models.User:
    return current_user


@router.post("/change-password", response_model=UserResponse)
def change_password(
    request: ChangePasswordRequest,
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    if not verify_password(request.old_password, current_user.password_hash):
        raise _invalid_credentials()
    current_user.password_hash = hash_password(request.new_password)
    current_user.token_version += 1
    return current_user
