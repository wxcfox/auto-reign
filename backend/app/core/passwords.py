from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets


_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 600_000
_SALT_BYTES = 16
_B64URL_ALPHABET = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    if not value or any(character not in _B64URL_ALPHABET for character in value):
        raise ValueError("Invalid base64url section.")
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(
        f"{value}{padding}".encode("ascii"),
        altchars=b"-_",
        validate=True,
    )


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _ITERATIONS,
    )
    return f"{_ALGORITHM}${_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt_text, digest_text = password_hash.split("$", 3)
        if algorithm != _ALGORITHM:
            return False
        if iterations_text != str(_ITERATIONS):
            return False
        salt = _b64decode(salt_text)
        if len(salt) != _SALT_BYTES:
            return False
        expected_digest = _b64decode(digest_text)

        actual_digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            _ITERATIONS,
        )
    except (binascii.Error, UnicodeEncodeError, ValueError, TypeError):
        return False

    return hmac.compare_digest(actual_digest, expected_digest)
