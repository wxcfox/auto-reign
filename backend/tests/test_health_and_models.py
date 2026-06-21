from fastapi.testclient import TestClient


def test_health_reports_local_dependencies(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["storage"]["mysql"] == "configured"
    assert body["storage"]["qdrant"] == "configured"
    assert "providers" in body


def test_models_only_returns_configured_providers(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("QWEN_API_KEY", "qwen-test")
    monkeypatch.setenv("QWEN_CHAT_MODELS", "qwen3.7-plus,qwen3.7-max,qwen3.7-max")
    from app.core.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as configured_client:
            response = configured_client.get("/api/models")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 200
    body = response.json()
    assert body["providers"] == [
        {"provider": "qwen", "models": ["qwen3.7-plus", "qwen3.7-max", "qwen3.7-max"]}
    ]
    assert "qwen-test" not in response.text


def test_frontend_origin_is_allowed(client: TestClient) -> None:
    response = client.options(
        "/api/documents",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
