from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import TokenInvalidError, decode_access_token
from app.core.errors import forbidden
from app.db import models
from app.db.session import session_scope


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


SessionDep = Annotated[
    Session,
    Depends(get_session, scope="function"),
]


def _auth_error(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=401,
        detail={"code": code, "message": message},
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    session: SessionDep,
    authorization: str = Header(default=""),
) -> models.User:
    if not authorization:
        raise _auth_error("auth_required", "Authentication is required.")

    scheme, separator, token = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not token.strip():
        raise _auth_error("token_invalid", "Bearer token is invalid.")

    try:
        payload = decode_access_token(token.strip())
    except TokenInvalidError as exc:
        raise _auth_error("token_invalid", "Bearer token is invalid.") from exc

    user = session.get(models.User, payload.user_id)
    if user is None or not user.is_active:
        raise _auth_error("user_inactive", "User is inactive or unavailable.")
    if user.username != payload.username or user.token_version != payload.token_version:
        raise _auth_error("token_revoked", "Bearer token has been revoked.")
    return user


def get_optional_current_user(
    session: SessionDep,
    authorization: str = Header(default=""),
) -> models.User | None:
    if not authorization:
        return None
    return get_current_user(session=session, authorization=authorization)


def get_current_admin(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    if current_user.role != "admin":
        raise forbidden(
            "admin_required",
            "Administrator access is required.",
        )
    return current_user
