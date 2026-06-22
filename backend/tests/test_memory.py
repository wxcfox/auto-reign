from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import get_settings


def test_finish_generates_report_and_updates_memory(client: TestClient) -> None:
    config = {
        "target_company": "OpenAI",
        "target_role": "Backend Engineer",
        "job_description": "Build AI app infrastructure.",
        "extra_prompt": "Focus on weakness reinforcement.",
        "language": "zh-CN",
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
    report_path = Path(body["report"]["report_path"])
    weakness_path = settings.data_dir / "memory" / "weakness_memory.md"
    interview_history_path = settings.data_dir / "memory" / "interview_history.md"
    learning_profile_path = settings.data_dir / "memory" / "learning_profile.md"
    assert report_path.exists()
    assert weakness_path.exists()
    assert interview_history_path.exists()
    assert learning_profile_path.exists()
    assert "# 面试复盘报告" in report_path.read_text(encoding="utf-8")

    memory = client.get("/api/memory")
    assert memory.status_code == 200
    assert "# 薄弱项记忆" in memory.json()["files"]["weakness"]["content"]
    assert "## 当前薄弱项总结" in weakness_path.read_text(encoding="utf-8")
