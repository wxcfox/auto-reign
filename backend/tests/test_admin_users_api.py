from __future__ import annotations

import pytest

from app.db import models
from app.db.session import session_scope

VALID_PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "another correct horse battery staple"


def _admin_id(client, admin_headers) -> int:
    response = client.get("/api/admin/users", headers=admin_headers)
    assert response.status_code == 200
    return next(
        user["id"] for user in response.json()["users"] if user["role"] == "admin"
    )


def _token_version(client, user_id: int) -> int:
    with session_scope(client.app.state.session_factory) as session:
        user = session.get(models.User, user_id)
        assert user is not None
        return user.token_version


def test_admin_creates_an_ordinary_user(client, admin_headers) -> None:
    response = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "alice",
            "display_name": "Alice",
            "password": "correct horse battery staple",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "id": response.json()["id"],
        "username": "alice",
        "display_name": "Alice",
        "role": "user",
        "is_active": True,
        "created_at": response.json()["created_at"],
        "updated_at": response.json()["updated_at"],
    }

    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": VALID_PASSWORD},
    )
    assert login.status_code == 200


def test_create_user_defaults_empty_display_name_to_username(
    client, admin_headers
) -> None:
    response = client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "alice", "display_name": "", "password": VALID_PASSWORD},
    )

    assert response.status_code == 201
    assert response.json()["display_name"] == "alice"


def test_admin_lists_admin_and_ordinary_users_sorted_by_username(
    client, admin_headers, create_user
) -> None:
    create_user(username="zoe")
    create_user(username="bob")

    response = client.get("/api/admin/users", headers=admin_headers)

    assert response.status_code == 200
    assert [user["username"] for user in response.json()["users"]] == [
        "admin",
        "bob",
        "zoe",
    ]


def test_admin_cannot_create_duplicate_username(client, admin_headers) -> None:
    payload = {
        "username": "alice",
        "display_name": "Alice",
        "password": VALID_PASSWORD,
    }
    first = client.post("/api/admin/users", headers=admin_headers, json=payload)
    second = client.post("/api/admin/users", headers=admin_headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "username_taken"


@pytest.mark.parametrize(
    "payload",
    [
        {"username": "ab", "display_name": "Alice", "password": VALID_PASSWORD},
        {
            "username": "not allowed",
            "display_name": "Alice",
            "password": VALID_PASSWORD,
        },
        {"username": "alice", "display_name": "A" * 121, "password": VALID_PASSWORD},
        {"username": "alice", "display_name": "Alice", "password": "short"},
    ],
)
def test_create_user_validates_request(client, admin_headers, payload) -> None:
    response = client.post("/api/admin/users", headers=admin_headers, json=payload)

    assert response.status_code == 422


def test_disabling_user_revokes_existing_token(
    client, admin_headers, create_user
) -> None:
    user, headers = create_user()

    response = client.patch(
        f"/api/admin/users/{user['id']}/status",
        headers=admin_headers,
        json={"is_active": False},
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is False
    revoked = client.get("/api/auth/me", headers=headers)
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "user_inactive"


def test_reenabling_user_keeps_old_token_revoked(
    client, admin_headers, create_user
) -> None:
    user, headers = create_user()
    path = f"/api/admin/users/{user['id']}/status"
    assert client.patch(
        path, headers=admin_headers, json={"is_active": False}
    ).status_code == 200

    response = client.patch(
        path, headers=admin_headers, json={"is_active": True}
    )

    assert response.status_code == 200
    assert response.json()["is_active"] is True
    revoked = client.get("/api/auth/me", headers=headers)
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "token_revoked"


def test_setting_same_status_is_idempotent_and_keeps_token_valid(
    client, admin_headers, create_user
) -> None:
    user, headers = create_user()
    version_before = _token_version(client, user["id"])

    response = client.patch(
        f"/api/admin/users/{user['id']}/status",
        headers=admin_headers,
        json={"is_active": True},
    )

    assert response.status_code == 200
    assert _token_version(client, user["id"]) == version_before
    assert client.get("/api/auth/me", headers=headers).status_code == 200


def test_setting_inactive_status_twice_only_revokes_token_once(
    client, admin_headers, create_user
) -> None:
    user, _headers = create_user()
    path = f"/api/admin/users/{user['id']}/status"
    first = client.patch(
        path,
        headers=admin_headers,
        json={"is_active": False},
    )
    assert first.status_code == 200
    version_after_first_change = _token_version(client, user["id"])

    second = client.patch(
        path,
        headers=admin_headers,
        json={"is_active": False},
    )

    assert second.status_code == 200
    assert _token_version(client, user["id"]) == version_after_first_change


def test_resetting_password_revokes_token_and_replaces_credentials(
    client, admin_headers, create_user
) -> None:
    user, headers = create_user()

    response = client.post(
        f"/api/admin/users/{user['id']}/reset-password",
        headers=admin_headers,
        json={"password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    revoked = client.get("/api/auth/me", headers=headers)
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "token_revoked"
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": VALID_PASSWORD},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": NEW_PASSWORD},
    ).status_code == 200


def test_resetting_to_same_password_still_revokes_existing_token(
    client, admin_headers, create_user
) -> None:
    user, headers = create_user()
    version_before = _token_version(client, user["id"])

    response = client.post(
        f"/api/admin/users/{user['id']}/reset-password",
        headers=admin_headers,
        json={"password": VALID_PASSWORD},
    )

    assert response.status_code == 200
    assert _token_version(client, user["id"]) == version_before + 1
    revoked = client.get("/api/auth/me", headers=headers)
    assert revoked.status_code == 401
    assert revoked.json()["detail"]["code"] == "token_revoked"


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("GET", "/api/admin/users", None),
        (
            "POST",
            "/api/admin/users",
            {"username": "bob", "display_name": "Bob", "password": VALID_PASSWORD},
        ),
        ("PATCH", "/api/admin/users/999/status", {"is_active": False}),
        (
            "POST",
            "/api/admin/users/999/reset-password",
            {"password": NEW_PASSWORD},
        ),
    ],
)
def test_ordinary_user_cannot_manage_users(
    client, ordinary_user_headers, method: str, path: str, payload
) -> None:
    response = client.request(
        method,
        path,
        headers=ordinary_user_headers,
        json=payload,
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "admin_required"


def test_anonymous_user_cannot_list_users(client) -> None:
    response = client.get("/api/admin/users")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("PATCH", "status", {"is_active": False}),
        ("POST", "reset-password", {"password": NEW_PASSWORD}),
    ],
)
def test_admin_cannot_manage_fixed_admin_with_ordinary_user_endpoints(
    client, admin_headers, method: str, suffix: str, payload
) -> None:
    admin_id = _admin_id(client, admin_headers)

    response = client.request(
        method,
        f"/api/admin/users/{admin_id}/{suffix}",
        headers=admin_headers,
        json=payload,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "fixed_admin_managed"


@pytest.mark.parametrize(
    ("method", "suffix", "payload"),
    [
        ("PATCH", "status", {"is_active": False}),
        ("POST", "reset-password", {"password": NEW_PASSWORD}),
    ],
)
def test_admin_update_reports_missing_user(
    client, admin_headers, method: str, suffix: str, payload
) -> None:
    response = client.request(
        method,
        f"/api/admin/users/999/{suffix}",
        headers=admin_headers,
        json=payload,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "user_not_found"


def test_password_reset_validates_password(client, admin_headers, create_user) -> None:
    user, _headers = create_user()

    response = client.post(
        f"/api/admin/users/{user['id']}/reset-password",
        headers=admin_headers,
        json={"password": "short"},
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        ("DELETE", "/api/admin/users/1", None),
        ("PATCH", "/api/admin/users/1", {"role": "admin"}),
    ],
)
def test_delete_and_role_update_routes_do_not_exist(
    client, admin_headers, method: str, path: str, payload
) -> None:
    response = client.request(method, path, headers=admin_headers, json=payload)

    assert response.status_code == 404
