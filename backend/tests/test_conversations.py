import json
from datetime import timedelta

from app.db import models
from app.db.session import session_scope
from app.repositories.conversation_repository import ConversationRepository


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


def _register(client, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _create_learning_conversation(client, headers: dict[str, str], text: str) -> dict[str, object]:
    response = client.post(
        "/api/workspace/learning-notes/stream",
        json={"text": text, "language": "zh-CN"},
        headers=headers,
    )
    assert response.status_code == 200
    return _sse_result(response.text)


def _create_interview_conversation(client, user_id: int) -> str:
    with session_scope(client.app.state.session_factory) as session:
        conversation = models.Conversation(
            user_id=user_id,
            kind="interview",
            title="Backend cache interview",
            summary_json={"last_message": "Redis cache penetration follow-up"},
        )
        session.add(conversation)
        session.flush()
        conversation_id = conversation.id
    return conversation_id


def test_conversation_history_merges_interviews_and_learning_rows(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)
    headers = _register(client, "alice")

    interview_id = _create_interview_conversation(client, user_id=1)
    learning_body = _create_learning_conversation(client, headers, "今天学习了 Redis 缓存穿透。")

    response = client.get("/api/conversations", headers=headers)

    assert response.status_code == 200
    body = response.json()
    kinds = {item["kind"] for item in body["conversations"]}
    assert {"interview", "learning"}.issubset(kinds)
    assert all("status" not in item for item in body["conversations"])
    assert any(
        item["href"] == f"/interview?session={interview_id}"
        for item in body["conversations"]
    )
    assert any(
        item["href"] == f"/learn?session={learning_body['conversation_id']}"
        for item in body["conversations"]
    )


def test_conversation_detail_projects_learning_messages(client, monkeypatch) -> None:
    _stub_index_rebuild(monkeypatch)
    headers = _register(client, "alice")
    conversation_id = _create_learning_conversation(client, headers, "学习 MySQL 覆盖索引。")[
        "conversation_id"
    ]

    response = client.get(f"/api/conversations/{conversation_id}", headers=headers)

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
    headers = _register(client, "alice")
    learning_body = _create_learning_conversation(client, headers, "今天学习了 MySQL 覆盖索引。")
    conversation_id = learning_body["conversation_id"]
    artifact_id = learning_body["artifact"]["id"]

    rename_response = client.patch(
        f"/api/conversations/{conversation_id}",
        json={"title": "  MySQL 索引复习  "},
        headers=headers,
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["title"] == "MySQL 索引复习"
    assert any(
        item["id"] == conversation_id and item["title"] == "MySQL 索引复习"
        for item in client.get("/api/conversations", headers=headers).json()["conversations"]
    )

    delete_response = client.delete(f"/api/conversations/{conversation_id}", headers=headers)

    assert delete_response.status_code == 200
    assert delete_response.json() == {"id": conversation_id, "status": "deleted"}
    assert client.get(f"/api/conversations/{conversation_id}", headers=headers).status_code == 404
    assert all(
        item["id"] != conversation_id
        for item in client.get("/api/conversations", headers=headers).json()["conversations"]
    )
    assert client.get(f"/api/workspace/artifacts/{artifact_id}", headers=headers).status_code == 200


def test_renaming_learning_conversation_preserves_created_time_history_order(
    client, monkeypatch
) -> None:
    _stub_index_rebuild(monkeypatch)
    headers = _register(client, "alice")
    first = _create_learning_conversation(client, headers, "先学习 Redis 缓存穿透。")
    second = _create_learning_conversation(client, headers, "后学习 MySQL 覆盖索引。")
    first_id = first["conversation_id"]
    second_id = second["conversation_id"]

    before_ids = [
        item["id"]
        for item in client.get("/api/conversations", headers=headers).json()["conversations"]
    ]
    assert before_ids.index(second_id) < before_ids.index(first_id)

    rename_response = client.patch(
        f"/api/conversations/{first_id}",
        json={"title": "Redis 缓存穿透复习"},
        headers=headers,
    )

    assert rename_response.status_code == 200
    after = client.get("/api/conversations", headers=headers).json()["conversations"]
    after_ids = [item["id"] for item in after]
    assert after_ids.index(second_id) < after_ids.index(first_id)
    assert next(item for item in after if item["id"] == first_id)["title"] == "Redis 缓存穿透复习"


def test_interview_conversation_row_can_be_renamed_and_deleted(client) -> None:
    headers = _register(client, "alice")
    session_id = _create_interview_conversation(client, user_id=1)

    rename_response = client.patch(
        f"/api/conversations/{session_id}",
        json={"title": "缓存专项面试"},
        headers=headers,
    )

    assert rename_response.status_code == 200
    assert rename_response.json()["kind"] == "interview"
    assert rename_response.json()["title"] == "缓存专项面试"
    assert any(
        item["id"] == session_id and item["title"] == "缓存专项面试"
        for item in client.get("/api/conversations", headers=headers).json()["conversations"]
    )

    delete_response = client.delete(f"/api/conversations/{session_id}", headers=headers)

    assert delete_response.status_code == 200
    assert client.get(f"/api/conversations/{session_id}", headers=headers).status_code == 404


def test_conversation_repository_add_message_updates_conversation_timestamp(client) -> None:
    _register(client, "alice")
    repository = ConversationRepository()

    with session_scope(client.app.state.session_factory) as session:
        conversation = repository.create(
            session,
            user_id=1,
            kind="learning",
            title="Redis",
        )
        original_updated_at = conversation.updated_at - timedelta(days=1)
        conversation.updated_at = original_updated_at
        session.flush()

        repository.add_message(
            session,
            user_id=1,
            conversation_id=conversation.id,
            role="user",
            message_type="learning_note",
            content="缓存击穿",
        )

        assert conversation.updated_at > original_updated_at
