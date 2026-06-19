from fastapi.testclient import TestClient


CONFIG = {
    "target_company": "OpenAI",
    "target_role": "Backend Engineer",
    "job_description": "Build reliable AI application backends.",
    "extra_prompt": "Focus on RAG and FastAPI.",
    "mode": "comprehensive",
    "chat_model_provider": "openai",
    "chat_model": "gpt-4.1-mini",
    "target_rounds": 3,
}


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

    next_question = client.post(f"/api/interview-sessions/{session_id}/next-question")
    assert next_question.status_code == 200
    assert next_question.json()["turn"]["round_index"] == 2


def test_completed_session_rejects_answer(client: TestClient) -> None:
    created = client.post("/api/interview-sessions", json=CONFIG).json()
    session_id = created["session"]["id"]
    client.post(f"/api/interview-sessions/{session_id}/finish")
    response = client.post(f"/api/interview-sessions/{session_id}/answer", json={"answer": "late"})
    assert response.status_code == 409
