from fastapi import APIRouter, Depends, status

from app.api.dependencies import SessionDep, get_current_admin
from app.db import models
from app.repositories.user_repository import UserRepository
from app.schemas.admin_users import (
    AdminUserCreateRequest,
    AdminUserListResponse,
    AdminUserPasswordResetRequest,
    AdminUserStatusRequest,
)
from app.schemas.auth import UserResponse
from app.services.admin_user_service import AdminUserService

router = APIRouter(
    prefix="/api/admin/users",
    dependencies=[Depends(get_current_admin)],
)


@router.get("", response_model=AdminUserListResponse)
def list_users(session: SessionDep) -> AdminUserListResponse:
    return AdminUserListResponse(users=UserRepository().list_users(session))


@router.post(
    "",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_user(
    payload: AdminUserCreateRequest,
    session: SessionDep,
) -> models.User:
    return AdminUserService().create_user(session, payload)


@router.patch("/{user_id}/status", response_model=UserResponse)
def set_user_status(
    user_id: int,
    payload: AdminUserStatusRequest,
    session: SessionDep,
) -> models.User:
    return AdminUserService().set_active(session, user_id, payload.is_active)


@router.post("/{user_id}/reset-password", response_model=UserResponse)
def reset_user_password(
    user_id: int,
    payload: AdminUserPasswordResetRequest,
    session: SessionDep,
) -> models.User:
    return AdminUserService().reset_password(session, user_id, payload.password)
