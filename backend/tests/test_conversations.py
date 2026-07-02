import json


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
        def rebuild_index(self, session_factory, workspace, repository) -> str:
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)


def _create_learning_conversation(client, text: str) -> dict[str, object]:
    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": text, "language": "zh-CN"},
    )
    assert response.status_code == 200
    return _sse_result(response.text)


def test_conversation_history_merges_interviews_and_learning(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)

    interview = client.post(
        "/api/interview-sessions",
        json={
            "target_company": "",
            "target_role": "",
            "job_description": "",
            "extra_prompt": "Backend cache interview",
            "language": "zh-CN",
            "mode": "comprehensive",
            "chat_model_provider": "qwen",
            "chat_model": "qwen3.7-plus",
            "target_rounds": 1,
        },
    ).json()
    learning_body = _create_learning_conversation(client, "今天学习了 Redis 缓存穿透。")

    response = client.get("/api/conversations")

    assert response.status_code == 200
    body = response.json()
    kinds = {item["kind"] for item in body["conversations"]}
    assert {"interview", "learning"}.issubset(kinds)
    assert all("status" not in item for item in body["conversations"])
    assert any(
        item["href"] == f"/interview?session={interview['session']['id']}"
        for item in body["conversations"]
    )
    assert any(
        item["href"] == f"/learn?session={learning_body['conversation_id']}"
        for item in body["conversations"]
    )


def test_conversation_detail_projects_learning_messages(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)
    conversation_id = _create_learning_conversation(client, "学习 MySQL 覆盖索引。")[
        "conversation_id"
    ]

    response = client.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conversation_id
    assert body["kind"] == "learning"
    assert body["href"] == f"/learn?session={conversation_id}"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "assistant"


def test_learning_conversation_can_be_renamed_and_deleted_without_removing_artifact(
    client, monkeypatch
) -> None:
    _stub_index_rebuild(monkeypatch)
    learning_body = _create_learning_conversation(client, "今天学习了 MySQL 覆盖索引。")
    conversation_id = learning_body["conversation_id"]
    artifact_id = learning_body["artifact"]["id"]

    rename_response = client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "  MySQL 索引复习  "},
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["title"] == "MySQL 索引复习"
    assert any(
        item["id"] == conversation_id and item["title"] == "MySQL 索引复习"
        for item in client.get("/api/conversations").json()["conversations"]
    )

    delete_response = client.delete(f"/api/conversations/{conversation_id}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"id": conversation_id, "status": "deleted"}
    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert all(
        item["id"] != conversation_id
        for item in client.get("/api/conversations").json()["conversations"]
    )
    assert client.get(f"/api/workspace/artifacts/{artifact_id}").status_code == 200


def test_renaming_learning_conversation_preserves_created_time_history_order(
    client, monkeypatch
) -> None:
    _stub_index_rebuild(monkeypatch)
    first = _create_learning_conversation(client, "先学习 Redis 缓存穿透。")
    second = _create_learning_conversation(client, "后学习 MySQL 覆盖索引。")
    first_id = first["conversation_id"]
    second_id = second["conversation_id"]

    before_ids = [item["id"] for item in client.get("/api/conversations").json()["conversations"]]
    assert before_ids.index(second_id) < before_ids.index(first_id)

    rename_response = client.patch(
        f"/api/conversations/{first_id}",
        json={"title": "Redis 缓存穿透复习"},
    )

    assert rename_response.status_code == 200
    after = client.get("/api/conversations").json()["conversations"]
    after_ids = [item["id"] for item in after]
    assert after_ids.index(second_id) < after_ids.index(first_id)
    assert next(item for item in after if item["id"] == first_id)["title"] == "Redis 缓存穿透复习"


def test_interview_conversation_can_be_renamed_and_deleted(client) -> None:
    interview = client.post(
        "/api/interview-sessions",
        json={
            "target_company": "",
            "target_role": "",
            "job_description": "",
            "extra_prompt": "Backend cache interview",
            "language": "zh-CN",
            "mode": "comprehensive",
            "chat_model_provider": "qwen",
            "chat_model": "qwen3.7-plus",
            "target_rounds": 1,
        },
    ).json()
    session_id = interview["session"]["id"]

    rename_response = client.patch(
        f"/api/conversations/{session_id}",
        json={"title": "缓存专项面试"},
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["kind"] == "interview"
    assert rename_response.json()["title"] == "缓存专项面试"
    assert any(
        item["id"] == session_id and item["title"] == "缓存专项面试"
        for item in client.get("/api/conversations").json()["conversations"]
    )

    delete_response = client.delete(f"/api/conversations/{session_id}")

    assert delete_response.status_code == 200
    assert client.get(f"/api/conversations/{session_id}").status_code == 404
    assert client.get(f"/api/interview-sessions/{session_id}").status_code == 404
