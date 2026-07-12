from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import conflict, not_found
from app.core.passwords import hash_password
from app.db import models
from app.repositories.user_repository import UserRepository
from app.schemas.admin_users import AdminUserCreateRequest


class AdminUserService:
    def __init__(self, users: UserRepository | None = None) -> None:
        self.users = users or UserRepository()

    def create_user(
        self,
        session: Session,
        payload: AdminUserCreateRequest,
    ) -> models.User:
        user = models.User(
            username=payload.username,
            display_name=payload.display_name or payload.username,
            password_hash=hash_password(payload.password),
            role="user",
            is_active=True,
            token_version=1,
            settings_json={},
        )
        session.add(user)
        try:
            session.flush()
        except IntegrityError as error:
            raise conflict(
                "username_taken",
                "Username is already registered.",
            ) from error
        return user

    def set_active(
        self,
        session: Session,
        user_id: int,
        is_active: bool,
    ) -> models.User:
        user = self._ordinary_user_for_update(session, user_id)
        if user.is_active != is_active:
            user.is_active = is_active
            user.token_version += 1
        session.flush()
        return user

    def reset_password(
        self,
        session: Session,
        user_id: int,
        password: str,
    ) -> models.User:
        user = self._ordinary_user_for_update(session, user_id)
        user.password_hash = hash_password(password)
        user.token_version += 1
        session.flush()
        return user

    def _ordinary_user_for_update(
        self,
        session: Session,
        user_id: int,
    ) -> models.User:
        user = self.users.get_for_update(session, user_id)
        if user is None:
            raise not_found("user_not_found", "User not found.")
        if user.role != "user":
            raise conflict(
                "fixed_admin_managed",
                "The fixed administrator is not managed here.",
            )
        return user
