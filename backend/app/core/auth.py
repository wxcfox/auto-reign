from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings


class TokenInvalidError(ValueError):
    pass


@dataclass(frozen=True)
class AccessTokenPayload:
    username: str
    user_id: int
    token_version: int
    expires_at: datetime


def _b64encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode_json(value: str) -> dict[str, Any]:
    padding = "=" * (-len(value) % 4)
    try:
        raw = base64.b64decode(
            f"{value}{padding}".encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        data = json.loads(raw.decode("utf-8"))
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise TokenInvalidError("JWT section is malformed.") from exc
    if not isinstance(data, dict):
        raise TokenInvalidError("JWT payload must be an object.")
    return data


def _sign(message: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), message.encode("ascii"), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def create_access_token(
    username: str,
    user_id: int,
    token_version: int,
    expires_at: datetime | None = None,
) -> str:
    settings = get_settings()
    expire = expires_at or (
        datetime.now(UTC) + timedelta(minutes=settings.access_token_expire_minutes)
    )
    header = _b64encode_json({"alg": "HS256", "typ": "JWT"})
    payload = _b64encode_json(
        {
            "sub": username,
            "user_id": user_id,
            "token_version": token_version,
            "exp": int(expire.timestamp()),
        }
    )
    signing_input = f"{header}.{payload}"
    signature = _sign(signing_input, settings.jwt_secret_key)
    return f"{signing_input}.{signature}"


def decode_access_token(token: str) -> AccessTokenPayload:
    settings = get_settings()
    try:
        header, payload, signature = token.split(".", 2)
        header.encode("ascii")
        payload.encode("ascii")
        signature.encode("ascii")
    except ValueError as exc:
        raise TokenInvalidError("Malformed JWT.") from exc
    except UnicodeEncodeError as exc:
        raise TokenInvalidError("JWT section is malformed.") from exc

    expected_signature = _sign(f"{header}.{payload}", settings.jwt_secret_key)
    if not hmac.compare_digest(signature, expected_signature):
        raise TokenInvalidError("JWT signature is invalid.")

    header_data = _b64decode_json(header)
    if header_data.get("alg") != "HS256" or header_data.get("typ") != "JWT":
        raise TokenInvalidError("JWT header is invalid.")

    payload_data = _b64decode_json(payload)
    username = payload_data.get("sub")
    user_id = payload_data.get("user_id")
    token_version = payload_data.get("token_version")
    exp = payload_data.get("exp")
    if not isinstance(username, str) or not username:
        raise TokenInvalidError("JWT subject is missing.")
    if not isinstance(user_id, int) or user_id <= 0:
        raise TokenInvalidError("JWT user_id is missing.")
    if not isinstance(token_version, int) or token_version < 0:
        raise TokenInvalidError("JWT token_version is missing.")
    if not isinstance(exp, int):
        raise TokenInvalidError("JWT exp is missing.")

    expires_at = datetime.fromtimestamp(exp, UTC)
    if expires_at <= datetime.now(UTC):
        raise TokenInvalidError("JWT is expired.")
    return AccessTokenPayload(
        username=username,
        user_id=user_id,
        token_version=token_version,
        expires_at=expires_at,
    )
