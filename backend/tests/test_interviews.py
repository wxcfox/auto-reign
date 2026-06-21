from fastapi.testclient import TestClient


DEFAULT_QWEN_CONFIG = {
    "target_company": "",
    "target_role": "",
    "job_description": "",
    "extra_prompt": "",
    "mode": "comprehensive",
    "chat_model_provider": "qwen",
    "chat_model": "qwen3.7-plus",
    "target_rounds": 3,
}


CONFIG = {
    "target_company": "OpenAI",
    "target_role": "Backend Engineer",
    "job_description": "Build reliable AI application backends.",
    "extra_prompt": "Focus on RAG and FastAPI.",
    "mode": "comprehensive",
    "chat_model_provider": "qwen",
    "chat_model": "qwen3.7-plus",
    "target_rounds": 3,
}


def test_get_last_config_defaults_to_qwen(client: TestClient) -> None:
    response = client.get("/api/interview-configs/last")
    assert response.status_code == 200
    body = response.json()
    assert {key: body[key] for key in DEFAULT_QWEN_CONFIG} == DEFAULT_QWEN_CONFIG


def test_save_last_config_and_create_session(client: TestClient) -> None:
    saved = client.put("/api/interview-configs/last", json=CONFIG)
    assert saved.status_code == 200
    loaded = client.get("/api/interview-configs/last")
    assert loaded.status_code == 200
    assert loaded.json()["target_company"] == "OpenAI"

    created = client.post("/api/interview-sessions", json=CONFIG)
    assert created.status_code == 200
    body = created.json()
    assert body["session"]["status"] == "active"
    assert body["turn"]["round_index"] == 1
    assert body["turn"]["question"]


def test_create_session_skips_rag_when_library_is_empty(client: TestClient, monkeypatch) -> None:
    def fail_embed_texts(_self, _texts):
        raise AssertionError("embedding should not run for an empty library")

    monkeypatch.setattr("app.services.rag_service.RagService.embed_texts", fail_embed_texts)

    created = client.post("/api/interview-sessions", json=CONFIG)

    assert created.status_code == 200
    body = created.json()
    assert body["turn"]["question"]
    assert body["turn"]["retrieved_context_refs"] == []


def test_answer_feedback_follow_up_and_next_question(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]

    answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I would design services around clear repository and service boundaries."},
    )
    assert answer.status_code == 200
    body = answer.json()
    assert body["feedback"]
    assert isinstance(body["missing_points"], list)
    assert body["follow_up_question"]
    assert isinstance(body["weaknesses"], list)
    assert isinstance(body["review_suggestions"], list)

    follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "I would add retries, timeouts, and structured errors."},
    )
    assert follow_up.status_code == 200
    follow_up_body = follow_up.json()
    assert follow_up_body["feedback"]
    assert isinstance(follow_up_body["missing_points"], list)
    assert isinstance(follow_up_body["weaknesses"], list)
    assert isinstance(follow_up_body["review_suggestions"], list)

    next_question = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert next_question.status_code == 200
    assert next_question.json()["turn"]["round_index"] == 2


def test_completed_session_rejects_answer(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(f"/api/interview-sessions/{session_id}/finish")
    response = client.post(f"/api/interview-sessions/{session_id}/answer", json={"answer": "late"})
    assert response.status_code == 409


def test_next_question_requires_answer_and_respects_target_rounds(client: TestClient) -> None:
    config = {**CONFIG, "target_rounds": 1}
    created = client.post("/api/interview-sessions", json=config).json()
    session_id = created["session"]["id"]

    unanswered = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert unanswered.status_code == 409
    assert unanswered.json()["detail"]["code"] == "current_turn_unanswered"

    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "A concrete answer."},
    )
    target_reached = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert target_reached.status_code == 409
    assert target_reached.json()["detail"]["code"] == "target_rounds_reached"


def test_answers_cannot_be_submitted_twice(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]

    follow_up_before_answer = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "Too early."},
    )
    assert follow_up_before_answer.status_code == 409
    assert follow_up_before_answer.json()["detail"]["code"] == "main_answer_required"

    first_answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "First answer."},
    )
    assert first_answer.status_code == 200
    duplicate_answer = client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "Replacement answer."},
    )
    assert duplicate_answer.status_code == 409
    assert duplicate_answer.json()["detail"]["code"] == "answer_already_submitted"

    first_follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "First follow-up."},
    )
    assert first_follow_up.status_code == 200
    duplicate_follow_up = client.post(
        f"/api/interview-sessions/{session_id}/follow-up-answer",
        json={"answer": "Replacement follow-up."},
    )
    assert duplicate_follow_up.status_code == 409
    assert duplicate_follow_up.json()["detail"]["code"] == "follow_up_already_submitted"
