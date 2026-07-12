import json

from fastapi import HTTPException


def _register(client, username: str = "alice") -> dict[str, str]:
    response = client.post(
        "/api/auth/register",
        json={"username": username, "password": "correct horse battery staple"},
    )
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _sse_event(response, event: str) -> dict[str, object]:
    assert response.status_code == 200
    for frame in response.text.strip().split("\n\n"):
        lines = frame.splitlines()
        if f"event: {event}" not in lines:
            continue
        data = "\n".join(
            line.removeprefix("data:").strip()
            for line in lines
            if line.startswith("data:")
        )
        return json.loads(data)
    raise AssertionError(f"SSE response did not include {event}: {response.text}")


def test_general_chat_creates_and_continues_conversation_without_artifacts(client) -> None:
    headers = _register(client)
    first_response = client.post(
        "/api/chats/stream",
        json={
            "text": "Explain Python context managers.",
            "language": "en",
            "provider": "qwen",
            "model": "qwen3.7-plus",
        },
        headers=headers,
    )

    first = _sse_event(first_response, "result")
    conversation_id = str(first["conversation_id"])
    assert first["message"]["role"] == "assistant"

    second_response = client.post(
        "/api/chats/stream",
        json={
            "conversation_id": conversation_id,
            "text": "Show a short example.",
            "language": "en",
            "provider": "qwen",
            "model": "qwen3.7-plus",
        },
        headers=headers,
    )

    _sse_event(second_response, "result")
    detail = client.get(f"/api/conversations/{conversation_id}", headers=headers).json()
    assert detail["kind"] == "chat"
    assert detail["href"] == f"/chat?session={conversation_id}"
    assert detail["title"] == "Explain Python context managers."
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert client.get("/api/workspace/artifacts", headers=headers).json()["artifacts"] == []


def test_general_chat_rejects_another_users_conversation(client) -> None:
    alice = _register(client, "alice")
    created = _sse_event(
        client.post(
            "/api/chats/stream",
            json={"text": "Alice question", "language": "en"},
            headers=alice,
        ),
        "result",
    )
    bob = _register(client, "bob")

    response = client.post(
        "/api/chats/stream",
        json={
            "conversation_id": created["conversation_id"],
            "text": "Bob follow-up",
            "language": "en",
        },
        headers=bob,
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "chat_not_found"


def test_general_chat_rolls_back_when_provider_fails(client, monkeypatch) -> None:
    headers = _register(client)

    def fail_stream(*_args, **_kwargs):
        raise HTTPException(
            status_code=502,
            detail={"code": "provider_call_failed", "message": "Provider failed."},
        )
        yield "unreachable"

    monkeypatch.setattr("app.services.model_service.ModelService.stream_messages", fail_stream)

    response = client.post(
        "/api/chats/stream",
        json={"text": "Do not persist this", "language": "en"},
        headers=headers,
    )

    assert _sse_event(response, "error")["code"] == "provider_call_failed"
    assert client.get("/api/conversations", headers=headers).json()["conversations"] == []


def test_general_chat_rejects_blank_message(client) -> None:
    headers = _register(client)

    response = client.post(
        "/api/chats/stream",
        json={"text": "   ", "language": "en"},
        headers=headers,
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "chat_message_empty"


def test_general_chat_sends_only_conversation_messages_to_model(client, monkeypatch) -> None:
    headers = _register(client)
    captured: list[list[dict[str, str]]] = []

    def capture_stream(_self, messages, **_kwargs):
        captured.append(messages)
        yield "Direct answer"

    monkeypatch.setattr("app.services.model_service.ModelService.stream_messages", capture_stream)
    created = _sse_event(
        client.post(
            "/api/chats/stream",
            json={"text": "First question", "language": "en"},
            headers=headers,
        ),
        "result",
    )
    _sse_event(
        client.post(
            "/api/chats/stream",
            json={
                "conversation_id": created["conversation_id"],
                "text": "Follow-up question",
                "language": "en",
            },
            headers=headers,
        ),
        "result",
    )

    assert captured[0] == [{"role": "user", "content": "First question"}]
    assert captured[1] == [
        {"role": "user", "content": "First question"},
        {"role": "assistant", "content": "Direct answer"},
        {"role": "user", "content": "Follow-up question"},
    ]
    assert all(message["role"] != "system" for call in captured for message in call)
