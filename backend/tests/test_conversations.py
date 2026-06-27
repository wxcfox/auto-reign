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


def test_conversation_history_merges_interviews_and_learning(client, monkeypatch) -> None:
    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

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
    learning = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "今天学习了 Redis 缓存穿透。", "language": "zh-CN"},
    )
    learning_body = _sse_result(learning.text)

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
    class RecordingIndexService:
        def rebuild_index(self, session_factory, workspace, repository) -> str:
            return "rebuilt"

    monkeypatch.setattr("app.api.workspace.IndexService", RecordingIndexService)

    learning = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": "学习 MySQL 覆盖索引。", "language": "zh-CN"},
    )
    conversation_id = _sse_result(learning.text)["conversation_id"]

    response = client.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == conversation_id
    assert body["kind"] == "learning"
    assert body["href"] == f"/learn?session={conversation_id}"
    assert len(body["messages"]) == 2
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][1]["role"] == "assistant"
