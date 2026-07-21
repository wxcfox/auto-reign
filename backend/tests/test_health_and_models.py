from pathlib import Path

from fastapi.testclient import TestClient

from tests.fakes import FakeKnowledgeVectorStore


def test_health_reports_local_dependencies(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "development"
    assert body["storage"]["mysql"] == "configured"
    assert body["storage"]["elasticsearch"] == "configured"
    assert body["storage"]["qdrant"] == "configured"
    assert body["storage"]["object_store"] == "local"
    assert "providers" in body
    assert "workspace" not in body


def test_retriever_health_checks_both_shared_backends(client: TestClient) -> None:
    response = client.get("/api/health/retrievers")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "retrievers": {"elasticsearch": True, "qdrant": True},
    }


def test_retriever_health_reports_unavailable_without_hiding_backend(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        client.app.state.knowledge_retriever_factory,
        "test_connections",
        lambda: {"elasticsearch": False, "qdrant": True},
    )

    response = client.get("/api/health/retrievers")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "retrievers": {"elasticsearch": False, "qdrant": True},
    }


def test_health_reads_object_store_status_from_the_bound_app_settings(
    client: TestClient,
) -> None:
    original_settings = client.app.state.settings
    client.app.state.settings = original_settings.model_copy(
        update={"object_store_backend": "s3"}
    )
    try:
        response = client.get("/api/health")
    finally:
        client.app.state.settings = original_settings

    assert response.status_code == 200
    assert response.json()["storage"]["object_store"] == "s3"


def test_openapi_exposes_only_unified_core_routes(client: TestClient) -> None:
    paths = set(client.get("/openapi.json").json()["paths"])

    assert "/api/conversations/stream" in paths
    assert "/api/agents" in paths
    assert "/api/workspaces" in paths
    assert "/api/knowledge-collections" in paths
    assert "/api/chats/stream" not in paths
    assert not any(path.startswith("/api/interview-") for path in paths)
    assert "/api/reports" not in paths
    assert not any(
        path == "/api/workspace" or path.startswith("/api/workspace/")
        for path in paths
    )


def test_runtime_source_has_no_interview_or_learning_branch() -> None:
    runtime_files = [
        Path("app/services/agent_runtime.py"),
        Path("app/services/generation_service.py"),
        Path("app/services/model_service.py"),
        Path("app/api/conversations.py"),
    ]

    combined = "\n".join(path.read_text(encoding="utf-8") for path in runtime_files)

    assert 'kind == "interview"' not in combined
    assert 'kind == "learning"' not in combined
    assert "PromptId" not in combined


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
    from app.db.models import Base
    from app.db.session import create_engine_for_settings
    from app.main import create_app

    get_settings.cache_clear()
    try:
        engine = create_engine_for_settings(get_settings())
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            engine.dispose()
        with TestClient(
            create_app(
                knowledge_retriever_factory_override=FakeKnowledgeVectorStore(),
                start_background_workers=False,
            )
        ) as configured_client:
            response = configured_client.get("/api/models")
    finally:
        get_settings.cache_clear()
    assert response.status_code == 200
    body = response.json()
    assert body["providers"] == [
        {"provider": "qwen", "models": ["qwen3.7-plus", "qwen3.7-max"]}
    ]
    assert body["default"] == {"provider": "qwen", "model": "qwen3.7-plus"}
    assert "qwen-test" not in response.text


def test_models_does_not_fallback_when_default_provider_is_unconfigured(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
    monkeypatch.setenv("DEFAULT_CHAT_PROVIDER", "qwen")
    monkeypatch.setenv("QWEN_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test")
    monkeypatch.setenv("OPENAI_CHAT_MODELS", "openai-default,openai-secondary")
    from app.core.config import get_settings
    from app.db.models import Base
    from app.db.session import create_engine_for_settings
    from app.main import create_app

    get_settings.cache_clear()
    try:
        engine = create_engine_for_settings(get_settings())
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            engine.dispose()
        with TestClient(
            create_app(
                knowledge_retriever_factory_override=FakeKnowledgeVectorStore(),
                start_background_workers=False,
            )
        ) as configured_client:
            response = configured_client.get("/api/models")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json() == {
        "providers": [
            {
                "provider": "openai",
                "models": ["openai-default", "openai-secondary"],
            }
        ],
        "default": None,
    }


def test_app_startup_does_not_require_embedding_provider(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
    monkeypatch.setenv("DEFAULT_CHAT_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test")
    monkeypatch.setenv("DEEPSEEK_CHAT_MODELS", "deepseek-chat")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", str(tmp_path / "objects"))

    from app.core.config import get_settings
    from app.db.models import Base
    from app.db.session import create_engine_for_settings
    from app.main import create_app

    get_settings.cache_clear()
    try:
        engine = create_engine_for_settings(get_settings())
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            engine.dispose()
        with TestClient(create_app(start_background_workers=False)) as app_client:
            response = app_client.get("/api/health")
    finally:
        get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_frontend_origin_is_allowed(client: TestClient) -> None:
    response = client.options(
        "/api/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
