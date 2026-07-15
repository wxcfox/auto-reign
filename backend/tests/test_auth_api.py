from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select

from app.core.passwords import verify_password
from app.db import models
from app.db.session import session_scope


@dataclass
class CommitFailingClient:
    client: TestClient

    def post(self, *args, **kwargs):
        return self.client.post(*args, **kwargs)

    def read_admin_with_independent_session(self) -> models.User:
        with session_scope(self.client.app.state.session_factory) as session:
            admin = session.scalar(
                select(models.User).where(models.User.username == "admin")
            )
            assert admin is not None
            session.expunge(admin)
            return admin


@pytest.fixture
def commit_failing_client(client) -> Iterator[CommitFailingClient]:
    session_factory = client.app.state.session_factory
    should_fail = True

    def fail_first_commit(_session) -> None:
        nonlocal should_fail
        if should_fail:
            should_fail = False
            raise RuntimeError("simulated commit failure")

    event.listen(session_factory, "before_commit", fail_first_commit)
    failing_client = TestClient(client.app, raise_server_exceptions=False)
    try:
        yield CommitFailingClient(failing_client)
    finally:
        failing_client.close()
        event.remove(session_factory, "before_commit", fail_first_commit)


def test_pending_admin_is_discovered_through_me(client) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 400
    assert response.json()["detail"] == {
        "code": "admin_password_setup_required",
        "message": "The initial administrator password must be set.",
        "admin_username": "admin",
    }


def test_admin_password_setup_succeeds_once_and_returns_token(client) -> None:
    first = client.post(
        "/api/auth/admin-password/setup",
        json={"password": "correct horse battery staple"},
    )

    assert first.status_code == 200
    assert first.json()["access_token"]
    assert first.json()["user"]["username"] == "admin"
    assert first.json()["user"]["role"] == "admin"

    second = client.post(
        "/api/auth/admin-password/setup",
        json={"password": "another correct horse battery staple"},
    )

    assert second.status_code == 409
    assert second.json()["detail"]["code"] == "admin_password_already_initialized"


def test_admin_password_setup_rejects_password_shorter_than_minimum(client) -> None:
    response = client.post(
        "/api/auth/admin-password/setup",
        json={"password": "short"},
    )

    assert response.status_code == 422


def test_public_registration_route_does_not_exist(client) -> None:
    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )

    assert response.status_code == 404


def test_database_commit_finishes_before_success_response(commit_failing_client) -> None:
    response = commit_failing_client.post(
        "/api/auth/admin-password/setup",
        json={"password": "correct horse battery staple"},
    )

    assert response.status_code == 500
    admin = commit_failing_client.read_admin_with_independent_session()
    assert admin.credential_bootstrap_status == "pending"
    assert not verify_password("correct horse battery staple", admin.password_hash)


def test_login_and_me(client, admin_headers) -> None:
    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "correct horse battery staple"},
    )

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "admin"
    assert me_response.json()["role"] == "admin"
    assert admin_headers["Authorization"].startswith("Bearer ")


def test_change_password_revokes_old_token(client, admin_headers) -> None:
    old_token = admin_headers["Authorization"].removeprefix("Bearer ")

    change_response = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={
            "old_password": "correct horse battery staple",
            "new_password": "secret",
        },
    )

    assert change_response.status_code == 200
    revoked_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {old_token}"},
    )
    assert revoked_response.status_code == 401
    assert revoked_response.json()["detail"]["code"] == "token_revoked"

    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "secret"},
    )
    assert login_response.status_code == 200


def test_change_password_rejects_password_shorter_than_minimum(
    client, admin_headers
) -> None:
    response = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={
            "old_password": "correct horse battery staple",
            "new_password": "short",
        },
    )

    assert response.status_code == 422


def test_me_requires_bearer_token_after_admin_setup(client, admin_headers) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "auth_required"
    assert admin_headers["Authorization"].startswith("Bearer ")


def test_login_rejects_bad_password(client, admin_headers) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrong horse battery staple"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "invalid_credentials"
    assert admin_headers["Authorization"].startswith("Bearer ")


def test_me_rejects_malformed_authorization_header(client) -> None:
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Token abc.def.ghi"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "token_invalid"


def test_me_rejects_invalid_token(client) -> None:
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer not-a-token"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "token_invalid"


def test_me_rejects_inactive_user_token(client, admin_headers) -> None:
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    with session_scope(client.app.state.session_factory) as session:
        admin = session.scalar(select(models.User).where(models.User.username == "admin"))
        assert admin is not None
        admin.is_active = False

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "user_inactive"


def test_change_password_rejects_wrong_old_password(client, admin_headers) -> None:
    response = client.post(
        "/api/auth/change-password",
        headers=admin_headers,
        json={
            "old_password": "wrong horse battery staple",
            "new_password": "new correct horse battery staple",
        },
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "invalid_credentials"


def test_me_rejects_stale_token_after_db_version_increment(client, admin_headers) -> None:
    token = admin_headers["Authorization"].removeprefix("Bearer ")
    with session_scope(client.app.state.session_factory) as session:
        admin = session.scalar(select(models.User).where(models.User.username == "admin"))
        assert admin is not None
        admin.token_version += 1

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["detail"]["code"] == "token_revoked"
