from fastapi.testclient import TestClient


def test_health_reports_local_dependencies(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage"]["sqlite"] == "configured"
    assert body["storage"]["chroma"] == "configured"
    assert "providers" in body


def test_models_only_returns_configured_providers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "app.db"))
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    with TestClient(create_app()) as configured_client:
        response = configured_client.get("/api/models")
    assert response.status_code == 200
    body = response.json()
    assert body["providers"] == [
        {"provider": "openai", "models": ["gpt-4.1-mini", "gpt-4.1"]}
    ]
    assert "sk-test" not in response.text
