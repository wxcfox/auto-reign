from app.core.passwords import verify_password
from app.db import models
from app.db.session import session_scope


def test_register_returns_token_and_user(client):
    response = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "correct horse battery staple",
            "display_name": "Alice",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["token_type"] == "bearer"
    assert data["user"]["username"] == "alice"
    assert data["user"]["display_name"] == "Alice"
    assert "password" not in data["user"]
    assert "password_hash" not in data["user"]

    with session_scope(client.app.state.session_factory) as session:
        user = session.query(models.User).filter_by(username="alice").one()
        assert verify_password("correct horse battery staple", user.password_hash)
        assert user.settings_json == {
            "schema_version": 1,
            "language": "zh-CN",
            "active_collection": "auto_reign_user_1",
        }


def test_register_rejects_duplicate_username(client):
    payload = {"username": "alice", "password": "correct horse battery staple"}
    first_response = client.post("/api/auth/register", json=payload)
    assert first_response.status_code == 200

    response = client.post("/api/auth/register", json=payload)

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "username_taken"


def test_login_and_me(client):
    client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )

    login_response = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "correct horse battery staple"},
    )

    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert me_response.status_code == 200
    assert me_response.json()["username"] == "alice"


def test_change_password_revokes_old_token(client):
    register_response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correct horse battery staple"},
    )
    old_token = register_response.json()["access_token"]

    change_response = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {old_token}"},
        json={
            "old_password": "correct horse battery staple",
            "new_password": "new correct horse battery staple",
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
        json={"username": "alice", "password": "new correct horse battery staple"},
    )
    assert login_response.status_code == 200


def test_me_requires_bearer_token(client):
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"
