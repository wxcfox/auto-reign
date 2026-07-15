from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import Request
from fastapi.exceptions import RequestValidationError

from app.core.validation_errors import (
    remove_validation_inputs,
    request_validation_error_handler,
)


SHORT_SECRET = "Q7!z"
LONG_SECRET = "LongSecret-" + ("x" * (257 - len("LongSecret-")))
VALID_SECRET = "valid secret must never be echoed 6f9a"
NEW_VALID_SECRET = "new valid secret must never be echoed 8c2d"


class UnencodableInput:
    __slots__ = ()


def _contains_key(value: object, key: str) -> bool:
    if isinstance(value, dict):
        return key in value or any(
            _contains_key(item, key) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, key) for item in value)
    return False


def _assert_validation_error(
    response,
    *,
    secrets: tuple[str, ...],
    loc: tuple[str, ...],
    error_type: str,
    constraint: tuple[str, int] | None = None,
) -> None:
    assert response.status_code == 422
    assert all(secret not in response.text for secret in secrets)
    payload = response.json()
    assert not _contains_key(payload, "input")
    matching = [
        error
        for error in payload["detail"]
        if error.get("loc") == list(loc) and error.get("type") == error_type
    ]
    assert matching
    assert matching[0]["msg"]
    if constraint is not None:
        key, expected = constraint
        assert matching[0]["ctx"][key] == expected


def _assert_no_sensitive_user_fields(value: object) -> None:
    for key in ("password", "password_hash", "token_version"):
        assert not _contains_key(value, key)


def test_remove_validation_inputs_is_recursive() -> None:
    errors = [
        {
            "type": "example",
            "loc": ("body", "password"),
            "msg": "Keep diagnostics",
            "input": "outer-secret",
            "ctx": {
                "limit": 6,
                "input": "nested-secret",
                "items": [{"input": "deep-secret", "kept": True}],
            },
        }
    ]

    assert remove_validation_inputs(errors) == [
        {
            "type": "example",
            "loc": ("body", "password"),
            "msg": "Keep diagnostics",
            "ctx": {"limit": 6, "items": [{"kept": True}]},
        }
    ]


def test_handler_removes_unencodable_input_before_json_encoding() -> None:
    error = RequestValidationError(
        [
            {
                "type": "example_error",
                "loc": ("body", "value"),
                "msg": "Keep this diagnostic",
                "input": UnencodableInput(),
                "ctx": {"limit": 6},
            }
        ],
        body=UnencodableInput(),
    )

    response = asyncio.run(
        request_validation_error_handler(Request({"type": "http"}), error)
    )
    payload = json.loads(response.body)

    assert response.status_code == 422
    assert payload == {
        "detail": [
            {
                "type": "example_error",
                "loc": ["body", "value"],
                "msg": "Keep this diagnostic",
                "ctx": {"limit": 6},
            }
        ]
    }
    assert not _contains_key(payload, "input")


@pytest.mark.parametrize(
    ("secret", "error_type", "constraint"),
    [
        pytest.param(
            SHORT_SECRET,
            "string_too_short",
            ("min_length", 6),
            id="too-short",
        ),
        pytest.param(
            LONG_SECRET,
            "string_too_long",
            ("max_length", 256),
            id="too-long",
        ),
    ],
)
def test_admin_create_password_validation_is_sanitized(
    client,
    admin_headers,
    secret: str,
    error_type: str,
    constraint: tuple[str, int],
) -> None:
    response = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "alice", "display_name": "Alice", "password": secret},
    )

    _assert_validation_error(
        response,
        secrets=(secret,),
        loc=("body", "password"),
        error_type=error_type,
        constraint=constraint,
    )


@pytest.mark.parametrize(
    ("secret", "error_type", "constraint"),
    [
        pytest.param(
            SHORT_SECRET,
            "string_too_short",
            ("min_length", 6),
            id="too-short",
        ),
        pytest.param(
            LONG_SECRET,
            "string_too_long",
            ("max_length", 256),
            id="too-long",
        ),
    ],
)
def test_admin_reset_password_validation_is_sanitized(
    client,
    admin_headers,
    create_user,
    secret: str,
    error_type: str,
    constraint: tuple[str, int],
) -> None:
    user, _headers = create_user()

    response = client.post(
        f"/api/admin/users/{user['id']}/reset-password",
        headers=admin_headers,
        json={"password": secret},
    )

    _assert_validation_error(
        response,
        secrets=(secret,),
        loc=("body", "password"),
        error_type=error_type,
        constraint=constraint,
    )


def test_missing_admin_create_field_does_not_echo_valid_password(
    client,
    admin_headers,
) -> None:
    response = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"display_name": "Alice", "password": VALID_SECRET},
    )

    _assert_validation_error(
        response,
        secrets=(VALID_SECRET,),
        loc=("body", "username"),
        error_type="missing",
    )


def test_admin_setup_validation_is_sanitized(client) -> None:
    response = client.post(
        "/api/auth/admin-password/setup",
        json={"password": SHORT_SECRET},
    )

    _assert_validation_error(
        response,
        secrets=(SHORT_SECRET,),
        loc=("body", "password"),
        error_type="string_too_short",
        constraint=("min_length", 6),
    )


def test_login_missing_field_does_not_echo_password(client) -> None:
    response = client.post(
        "/api/auth/login",
        json={"password": VALID_SECRET},
    )

    _assert_validation_error(
        response,
        secrets=(VALID_SECRET,),
        loc=("body", "username"),
        error_type="missing",
    )


def test_change_password_missing_field_does_not_echo_old_password(
    client,
    admin_headers,
) -> None:
    response = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={"old_password": VALID_SECRET},
    )

    _assert_validation_error(
        response,
        secrets=(VALID_SECRET,),
        loc=("body", "new_password"),
        error_type="missing",
    )


def test_admin_user_success_responses_do_not_expose_credentials(
    client,
    admin_headers,
) -> None:
    created = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "alice",
            "display_name": "Alice",
            "password": VALID_SECRET,
        },
    )
    assert created.status_code == 201

    reset = client.post(
        f"/api/admin/users/{created.json()['id']}/reset-password",
        headers=admin_headers,
        json={"password": NEW_VALID_SECRET},
    )
    assert reset.status_code == 200

    listed = client.get("/api/admin/users", headers=admin_headers)
    assert listed.status_code == 200

    for response in (created, reset, listed):
        assert VALID_SECRET not in response.text
        assert NEW_VALID_SECRET not in response.text
        _assert_no_sensitive_user_fields(response.json())
