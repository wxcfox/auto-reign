import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.core.auth import (
    TokenInvalidError,
    create_access_token,
    decode_access_token,
)
from app.core.passwords import hash_password, verify_password


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64encode_json(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64encode(raw)


def _signed_token(header: str, payload: str, secret: str = "test-secret") -> str:
    signing_input = f"{header}.{payload}"
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}"


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


@pytest.mark.parametrize(
    "password_hash",
    [
        "",
        "not-a-password-hash",
        "pbkdf2_sha256$600000$AA",
        "argon2$600000$AA$AA",
        "pbkdf2_sha256$not-an-int$AA$AA",
        "pbkdf2_sha256$0$AA$AA",
        "pbkdf2_sha256$-1$AA$AA",
        "pbkdf2_sha256$600000$A$AA",
        "pbkdf2_sha256$600000$AA$A",
        "pbkdf2_sha256$600000$é$AA",
        "pbkdf2_sha256$600000$AA$é",
    ],
)
def test_verify_password_rejects_malformed_hashes(
    password_hash: str,
) -> None:
    assert verify_password("same password", password_hash) is False


def test_verify_password_rejects_malformed_base64_with_matching_digest() -> None:
    salt = b"salt"
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        "same password".encode("utf-8"),
        salt,
        1,
    )
    malformed_salt = f"{_b64encode(salt)}==*"
    password_hash = f"pbkdf2_sha256$1${malformed_salt}${_b64encode(digest)}"

    assert verify_password("same password", password_hash) is False


def test_access_token_round_trip(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token("alice", 7, 2)

    payload = decode_access_token(token)

    assert payload.username == "alice"
    assert payload.user_id == 7
    assert payload.token_version == 2
    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("header", "payload"),
    [
        (
            _b64encode(b"not-json"),
            _b64encode_json(
                {
                    "exp": 4_102_444_800,
                    "sub": "alice",
                    "token_version": 2,
                    "user_id": 7,
                }
            ),
        ),
        (
            _b64encode_json({"alg": "HS256", "typ": "JWT"}),
            _b64encode(b"\xff"),
        ),
    ],
)
def test_access_token_rejects_signed_malformed_json_sections(
    header: str,
    payload: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_token(header, payload)

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()


@pytest.mark.parametrize(
    "token",
    [
        "é.valid.signature",
        "valid.é.signature",
        "valid.valid.é",
    ],
)
def test_access_token_rejects_non_ascii_sections(token: str, monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

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
