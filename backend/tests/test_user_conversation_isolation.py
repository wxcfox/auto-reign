import json


def _register(client, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _sse_result(body: str) -> dict[str, object]:
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        if "event: result" not in lines:
            continue
        data = "\n".join(
            line.removeprefix("data:").strip()
            for line in lines
            if line.startswith("data:")
        )
        return json.loads(data)
    raise AssertionError("SSE response did not include a result event.")


def _stub_index_rebuild(monkeypatch) -> None:
    class RecordingIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)


def _create_learning_conversation(
    client,
    headers: dict[str, str],
    text: str,
) -> str:
    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": text, "language": "zh-CN"},
        headers=headers,
    )
    assert response.status_code == 200
    return str(_sse_result(response.text)["conversation_id"])


def test_conversation_lists_are_user_scoped_after_learning_notes(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)
    alice_headers = _register(client, "alice")
    bob_headers = _register(client, "bob")

    alice_conversation_id = _create_learning_conversation(
        client,
        alice_headers,
        "Alice 学习了 Redis 缓存穿透。",
    )
    bob_conversation_id = _create_learning_conversation(
        client,
        bob_headers,
        "Bob 学习了 MySQL 覆盖索引。",
    )

    alice_list = client.get("/api/conversations", headers=alice_headers)
    bob_list = client.get("/api/conversations", headers=bob_headers)

    assert alice_list.status_code == 200
    assert bob_list.status_code == 200
    assert [item["id"] for item in alice_list.json()["conversations"]] == [
        alice_conversation_id
    ]
    assert [item["id"] for item in bob_list.json()["conversations"]] == [
        bob_conversation_id
    ]


def test_user_cannot_read_another_users_conversation(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)
    alice_headers = _register(client, "alice")
    bob_headers = _register(client, "bob")
    alice_conversation_id = _create_learning_conversation(
        client,
        alice_headers,
        "Alice 学习了 Redis 缓存穿透。",
    )

    response = client.get(
        f"/api/conversations/{alice_conversation_id}",
        headers=bob_headers,
    )

    assert response.status_code == 404


def test_user_cannot_rename_or_delete_another_users_conversation(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)
    alice_headers = _register(client, "alice")
    bob_headers = _register(client, "bob")
    alice_conversation_id = _create_learning_conversation(
        client,
        alice_headers,
        "Alice 学习了 Redis 缓存穿透。",
    )

    rename_response = client.patch(
        f"/api/conversations/{alice_conversation_id}",
        json={"title": "Bob should not rename this"},
        headers=bob_headers,
    )
    delete_response = client.delete(
        f"/api/conversations/{alice_conversation_id}",
        headers=bob_headers,
    )
    alice_detail = client.get(
        f"/api/conversations/{alice_conversation_id}",
        headers=alice_headers,
    )

    assert rename_response.status_code == 404
    assert delete_response.status_code == 404
    assert alice_detail.status_code == 200
    assert alice_detail.json()["title"] != "Bob should not rename this"
