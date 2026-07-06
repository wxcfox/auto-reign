from datetime import UTC, datetime, timedelta

import pytest

from app.core.auth import (
    TokenInvalidError,
    create_access_token,
    decode_access_token,
)
from app.core.passwords import hash_password, verify_password


def test_hash_password_does_not_store_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert hashed.startswith("pbkdf2_sha256$")


def test_verify_password_accepts_correct_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("correct horse battery staple", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    hashed = hash_password("correct horse battery staple")

    assert verify_password("wrong password", hashed) is False


def test_hash_password_uses_unique_salt() -> None:
    first = hash_password("same password")
    second = hash_password("same password")

    assert first != second
    assert verify_password("same password", first) is True
    assert verify_password("same password", second) is True


def test_access_token_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token(username="alice", user_id=7, token_version=2)

    payload = decode_access_token(token)

    assert payload.username == "alice"
    assert payload.user_id == 7
    assert payload.token_version == 2
    get_settings.cache_clear()


def test_access_token_rejects_expired_token(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token(
        username="alice",
        user_id=7,
        token_version=2,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()
