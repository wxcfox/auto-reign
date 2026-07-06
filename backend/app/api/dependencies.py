from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.auth import TokenInvalidError, decode_access_token
from app.core.user_scope import UserScope, build_user_scope
from app.db import models
from app.db.session import session_scope


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


def _auth_error(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=401, detail={"code": code, "message": message})


def get_current_user(
    authorization: str = Header(default=""),
    session: Session = Depends(get_session),
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


def get_user_scope(
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> UserScope:
    return build_user_scope(request.app.state.settings, current_user)
