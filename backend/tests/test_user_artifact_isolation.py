def _register(client, username: str) -> str:
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    ).json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sse_result(body: str) -> dict[str, object]:
    import json

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


def test_users_can_create_same_learning_artifact_path_without_collision(
    client,
    monkeypatch,
) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")

    first = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 MySQL 覆盖索引。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    second = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 MySQL 覆盖索引。", "language": "zh-CN"},
        headers=_auth(bob),
    )

    assert first.status_code == 200
    assert second.status_code == 200
    data_dir = client.app.state.settings.data_dir
    assert (data_dir / "users" / "1" / "workspace" / "knowledge").exists()
    assert (data_dir / "users" / "2" / "workspace" / "knowledge").exists()


def test_user_cannot_read_other_users_artifact(client, monkeypatch) -> None:
    class NoopIndexService:
        def rebuild_index(self, *args, **kwargs) -> str:
            return "noop"

    monkeypatch.setattr("app.api.workspace.IndexService", NoopIndexService)
    alice = _register(client, "alice")
    bob = _register(client, "bob")
    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 Redis 缓存穿透。", "language": "zh-CN"},
        headers=_auth(alice),
    )
    artifact_id = _sse_result(response.text)["artifact"]["id"]

    forbidden = client.get(f"/api/workspace/artifacts/{artifact_id}", headers=_auth(bob))

    assert forbidden.status_code == 404
