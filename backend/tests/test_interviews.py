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
