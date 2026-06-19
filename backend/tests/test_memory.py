from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_finish_generates_report_and_updates_memory(client: TestClient) -> None:
    config = {
        "target_company": "OpenAI",
        "target_role": "Backend Engineer",
        "job_description": "Build AI app infrastructure.",
        "extra_prompt": "Focus on weakness reinforcement.",
        "mode": "weakness_reinforcement",
        "chat_model_provider": "openai",
        "chat_model": "gpt-4.1-mini",
        "target_rounds": 1,
    }
    created = client.post("/api/interview-sessions", json=config).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "I use tests and clear services."},
    )

    finished = client.post(f"/api/interview-sessions/{session_id}/finish")
    assert finished.status_code == 200
    body = finished.json()
    assert body["report"]["report_path"].endswith(".md")

    settings = get_settings()
    assert Path(body["report"]["report_path"]).exists()
    assert (settings.data_dir / "memory" / "weakness_memory.md").exists()
    assert (settings.data_dir / "memory" / "interview_history.md").exists()
    assert (settings.data_dir / "memory" / "learning_profile.md").exists()

    memory = client.get("/api/memory")
    assert memory.status_code == 200
    assert "Weakness Memory" in memory.json()["files"]["weakness"]["content"]
