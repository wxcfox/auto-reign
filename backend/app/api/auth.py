from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.api.dependencies import (
    SessionDep,
    get_current_user,
    get_optional_current_user,
)
from app.core.auth import create_access_token
from app.core.passwords import hash_password, verify_password
from app.db import models
from app.schemas.auth import (
    AdminPasswordSetupRequest,
    ChangePasswordRequest,
    LoginRequest,
    TokenResponse,
    UserResponse,
)
from app.services.bootstrap_service import BootstrapService, INITIAL_ADMIN_USERNAME

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


@router.post("/login", response_model=TokenResponse)
def login(
    request: LoginRequest,
    session: SessionDep,
) -> TokenResponse:
    user = session.scalar(
        select(models.User).where(models.User.username == request.username)
    )
    if user is None or not user.is_active:
        raise _invalid_credentials()
    if not verify_password(request.password, user.password_hash):
        raise _invalid_credentials()
    return _token_response(user)


@router.post("/admin-password/setup", response_model=TokenResponse)
def setup_admin_password(
    payload: AdminPasswordSetupRequest,
    session: SessionDep,
) -> TokenResponse:
    user = BootstrapService().setup_initial_admin_password(
        session,
        password=payload.password,
    )
    return _token_response(user)


@router.get("/me", response_model=UserResponse)
def me(
    session: SessionDep,
    current_user: models.User | None = Depends(get_optional_current_user),
) -> models.User:
    if current_user is None:
        admin = session.scalar(
            select(models.User).where(models.User.username == INITIAL_ADMIN_USERNAME)
        )
        if admin is not None and admin.credential_bootstrap_status == "pending":
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "admin_password_setup_required",
                    "message": "The initial administrator password must be set.",
                    "admin_username": INITIAL_ADMIN_USERNAME,
                },
            )
        raise HTTPException(
            status_code=401,
            detail={
                "code": "auth_required",
                "message": "Authentication is required.",
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
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
