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


def _standard_b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii").rstrip("=")


def _b64encode_json(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _b64encode(raw)


def _standard_b64encode_json(payload: object, *, ensure_ascii: bool = True) -> str:
    raw = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=ensure_ascii
    ).encode("utf-8")
    return _standard_b64encode(raw)


def _signed_token(header: str, payload: str, secret: str = "test-secret") -> str:
    signing_input = f"{header}.{payload}"
    signature = hmac.new(
        secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{signing_input}.{_b64encode(signature)}"


def _signed_access_token(payload: dict[str, object]) -> str:
    return _signed_token(
        _b64encode_json({"alg": "HS256", "typ": "JWT"}), _b64encode_json(payload)
    )


def _signed_access_token_with_header(
    header: dict[str, object],
    payload: dict[str, object],
) -> str:
    return _signed_token(_b64encode_json(header), _b64encode_json(payload))


def _valid_access_token_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "exp": 4_102_444_800,
        "sub": "alice",
        "token_version": 2,
        "user_id": 7,
    }
    payload.update(overrides)
    return payload


def _password_hash(password: str, salt: bytes, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${_b64encode(salt)}${_b64encode(digest)}"


def test_hash_password_does_not_store_plaintext() -> None:
    hashed = hash_password("correct horse battery staple")

    assert hashed != "correct horse battery staple"
    assert hashed.startswith("pbkdf2_sha256$")


def test_hash_password_uses_fixed_local_format() -> None:
    hashed = hash_password("correct horse battery staple")
    algorithm, iterations, salt, digest = hashed.split("$")

    assert algorithm == "pbkdf2_sha256"
    assert iterations == "600000"
    assert len(base64.urlsafe_b64decode(f"{salt}==".encode("ascii"))) == 16
    assert base64.urlsafe_b64decode(f"{digest}==".encode("ascii"))
    assert "=" not in salt
    assert "=" not in digest


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
    digest = hashlib.pbkdf2_hmac("sha256", b"same password", salt, 600_000)
    malformed_salt = f"{_b64encode(salt)}==*"
    password_hash = f"pbkdf2_sha256$600000${malformed_salt}${_b64encode(digest)}"

    assert verify_password("same password", password_hash) is False


def test_verify_password_rejects_nonstandard_iterations_with_matching_digest() -> None:
    password_hash = _password_hash("same password", b"1234567890123456", 1)

    assert verify_password("same password", password_hash) is False


@pytest.mark.parametrize("iterations_text", ["0600000", "+600000", " 600000"])
def test_verify_password_rejects_noncanonical_iteration_text(
    iterations_text: str,
) -> None:
    password_hash = _password_hash("same password", b"1234567890123456", 600_000)
    algorithm, _iterations, salt, digest = password_hash.split("$")
    noncanonical_hash = f"{algorithm}${iterations_text}${salt}${digest}"

    assert verify_password("same password", noncanonical_hash) is False


@pytest.mark.parametrize("salt", [b"short salt", b"12345678901234567"])
def test_verify_password_rejects_nonstandard_salt_length_with_matching_digest(
    salt: bytes,
) -> None:
    password_hash = _password_hash("same password", salt, 600_000)

    assert verify_password("same password", password_hash) is False


def test_verify_password_rejects_padded_base64_sections_with_matching_digest() -> None:
    password_hash = _password_hash("same password", b"1234567890123456", 600_000)
    algorithm, iterations, salt, digest = password_hash.split("$")
    padded_hash = f"{algorithm}${iterations}${salt}=${digest}="

    assert verify_password("same password", padded_hash) is False


def test_verify_password_rejects_standard_base64_salt_with_matching_digest() -> None:
    salt = bytes([251]) * 16
    digest = hashlib.pbkdf2_hmac("sha256", b"same password", salt, 600_000)
    password_hash = (
        f"pbkdf2_sha256$600000${_standard_b64encode(salt)}${_b64encode(digest)}"
    )

    assert verify_password("same password", password_hash) is False


def test_verify_password_rejects_standard_base64_digest_with_matching_digest() -> None:
    salt = b"1234567890123456"
    digest = hashlib.pbkdf2_hmac("sha256", b"same password", salt, 600_000)
    password_hash = (
        f"pbkdf2_sha256$600000${_b64encode(salt)}${_standard_b64encode(digest)}"
    )

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


@pytest.mark.parametrize("token", ["not-a-jwt", "header.payload"])
def test_access_token_rejects_malformed_token_structure(
    token: str,
    monkeypatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()


def test_access_token_rejects_invalid_signature(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token(_valid_access_token_payload())
    tampered_token = f"{token[:-1]}A"

    with pytest.raises(TokenInvalidError):
        decode_access_token(tampered_token)

    get_settings.cache_clear()


@pytest.mark.parametrize(
    "header",
    [
        {"alg": "none", "typ": "JWT"},
        {"alg": "HS256", "typ": "Bearer"},
        {"alg": "HS256"},
    ],
)
def test_access_token_rejects_invalid_header(
    header: dict[str, object],
    monkeypatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token_with_header(header, _valid_access_token_payload())

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()


@pytest.mark.parametrize(
    "payload",
    [
        {
            "exp": 4_102_444_800,
            "token_version": 2,
            "user_id": 7,
        },
        _valid_access_token_payload(sub=""),
        _valid_access_token_payload(sub=123),
    ],
)
def test_access_token_rejects_invalid_subject(
    payload: dict[str, object],
    monkeypatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token(payload)

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()


@pytest.mark.parametrize("user_id", [0, -1])
def test_access_token_rejects_non_positive_user_id(
    user_id: int,
    monkeypatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token(_valid_access_token_payload(user_id=user_id))

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()


def test_access_token_rejects_negative_token_version(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token(_valid_access_token_payload(token_version=-1))

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()


@pytest.mark.parametrize(
    ("header", "payload"),
    [
        (
            _standard_b64encode_json({"alg": "HS256", "typ": "JWT", "x": ">"}),
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
            _standard_b64encode_json(
                {
                    "exp": 4_102_444_800,
                    "sub": "alice",
                    "token_version": 2,
                    "user_id": 7,
                    "x": "¾",
                },
                ensure_ascii=False,
            ),
        ),
        (
            f"{_b64encode_json({'alg': 'HS256', 'typ': 'JWT'})}=",
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
            f"{_b64encode_json({'exp': 4_102_444_800, 'sub': 'alice', 'token_version': 2, 'user_id': 7})}=",
        ),
    ],
)
def test_access_token_rejects_non_base64url_json_sections(
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
    "payload",
    [
        {
            "exp": 4_102_444_800,
            "sub": "alice",
            "token_version": 2,
            "user_id": True,
        },
        {
            "exp": 4_102_444_800,
            "sub": "alice",
            "token_version": False,
            "user_id": 7,
        },
    ],
)
def test_access_token_rejects_boolean_integer_claims(
    payload: dict[str, object],
    monkeypatch,
) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token(payload)

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


def test_access_token_rejects_out_of_range_exp(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET_KEY", "test-secret")
    from app.core.config import get_settings

    get_settings.cache_clear()
    token = _signed_access_token(
        {
            "exp": 10**100,
            "sub": "alice",
            "token_version": 2,
            "user_id": 7,
        }
    )

    with pytest.raises(TokenInvalidError):
        decode_access_token(token)

    get_settings.cache_clear()
