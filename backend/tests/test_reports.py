from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import Report
from app.db.session import session_scope
from app.services.markdown_utils import markdown_list_items, markdown_sections


def test_finish_generates_workspace_report_and_updates_review_state(client: TestClient) -> None:
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
    assert body["report"]["report_path"].startswith("reports/")
    assert body["report"]["report_path"].endswith(".md")

    settings = get_settings()
    assert not (settings.data_dir / "reports").exists()
    assert not (settings.data_dir / "memory").exists()
    assert client.get("/api/memory").status_code == 404

    workspace = settings.workspace_dir
    practice_files = list((workspace / "practice").glob("**/*.md"))
    report_files = list((workspace / "reports").glob("*.md"))
    mastery_path = workspace / "state" / "mastery.md"
    status_path = workspace / "review" / "status.md"
    assert len(practice_files) == 1
    assert len(report_files) == 1
    practice_text = practice_files[0].read_text(encoding="utf-8")
    assert "# 模拟面试记录" in practice_text
    assert practice_text.count(f"## 会话 {session_id}") == 1
    assert f"## 会话 {session_id}" in practice_text
    assert "I use tests and clear services." in practice_text
    assert "# 掌握状态" in mastery_path.read_text(encoding="utf-8")
    status_text = status_path.read_text(encoding="utf-8")
    assert "# 复习状态" in status_text
    current_focus = markdown_list_items(markdown_sections(status_text)["当前重点"])
    assert len(current_focus) <= 3
    assert "# 面试复盘报告" in report_files[0].read_text(encoding="utf-8")
    report_detail = client.get(f"/api/reports/{body['report']['id']}")
    assert report_detail.status_code == 200
    assert "# 面试复盘报告" in report_detail.json()["content"]


def test_report_detail_rejects_legacy_absolute_report_path(client: TestClient) -> None:
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
    report_id = finished.json()["report"]["id"]

    with session_scope(client.app.state.session_factory) as session:
        report = session.get(Report, report_id)
        assert report is not None
        report.report_path = str(get_settings().data_dir / "reports" / "legacy.md")

    response = client.get(f"/api/reports/{report_id}")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "report_not_found"


def test_finish_does_not_write_vectors_inline(
    client: TestClient, monkeypatch
) -> None:
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
    calls: list[str] = []

    def fail_upsert(*_args, **_kwargs):
        calls.append("upsert")
        raise AssertionError("finish should not write vectors inline")

    monkeypatch.setattr(
        "app.services.workspace_vector_store.WorkspaceVectorStore.upsert_documents",
        fail_upsert,
    )

    finished = client.post(f"/api/interview-sessions/{session_id}/finish")

    assert finished.status_code == 200
    assert calls == []


def test_finish_uses_extra_prompt_as_target_context_when_structured_fields_are_blank(
    client: TestClient,
) -> None:
    config = {
        "target_company": "",
        "target_role": "",
        "job_description": "",
        "extra_prompt": "面试字节后端岗位，JD 关注缓存和高并发。",
        "language": "zh-CN",
        "mode": "comprehensive",
        "chat_model_provider": "qwen",
        "chat_model": "qwen3.7-plus",
        "target_rounds": 1,
    }
    created = client.post("/api/interview-sessions", json=config).json()
    session_id = created["session"]["id"]
    client.post(
        f"/api/interview-sessions/{session_id}/answer",
        json={"answer": "我会用 Redis、限流和监控说明高并发处理。"},
    )

    finished = client.post(f"/api/interview-sessions/{session_id}/finish")

    assert finished.status_code == 200
    assert "字节后端岗位" in finished.json()["report"]["summary"]
    workspace = get_settings().workspace_dir
    practice_files = list((workspace / "practice").glob("**/*.md"))
    assert len(practice_files) == 1
    practice_text = practice_files[0].read_text(encoding="utf-8")
    assert "出题要求：面试字节后端岗位，JD 关注缓存和高并发。" in practice_text
