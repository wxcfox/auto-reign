def _register(client, username: str) -> str:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def test_workspace_api_requires_auth(client) -> None:
    response = client.get("/api/workspace/files")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"


def test_user_scope_creates_user_directories(client) -> None:
    token = _register(client, "alice")

    response = client.get(
        "/api/workspace/files",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data_dir = client.app.state.settings.data_dir
    assert (data_dir / "users" / "1" / "workspace").exists()
    assert (data_dir / "users" / "1" / "tmp").exists()
    assert (data_dir / "users" / "1" / "exports").exists()


def test_interview_api_requires_auth(client) -> None:
    response = client.get("/api/interview-configs/last")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"


def test_conversation_api_requires_auth(client) -> None:
    response = client.get("/api/conversations")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"


def test_chat_api_requires_auth(client) -> None:
    response = client.post(
        "/api/chats/stream",
        json={"text": "hello", "language": "en"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"


def test_report_api_requires_auth(client) -> None:
    response = client.get("/api/reports")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "auth_required"
