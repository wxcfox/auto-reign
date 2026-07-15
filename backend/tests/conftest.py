from collections.abc import Iterator
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import create_engine_for_settings
from tests.fake_object_store import FakeObjectStore
from tests.fakes import (
    FakeKnowledgeVectorStore,
    FakeOpenAIClient,
    FakeOpenAIEmbeddings,
)


@pytest.fixture
def fake_knowledge_vector_store() -> FakeKnowledgeVectorStore:
    return FakeKnowledgeVectorStore()


@pytest.fixture
def client(
    tmp_path,
    monkeypatch,
    fake_knowledge_vector_store: FakeKnowledgeVectorStore,
) -> Iterator[object]:
    from fastapi.testclient import TestClient

    fake_object_store = FakeObjectStore()
    unused_local_root = tmp_path / "unused-local-object-store"
    default_local_root = (Path("data") / "objects").resolve()
    default_files_before = {
        (
            path.relative_to(default_local_root),
            path.stat().st_size,
            path.stat().st_mtime_ns,
        )
        for path in default_local_root.rglob("*")
        if path.is_file()
    }
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("QWEN_API_KEY", "test-qwen-key")
    monkeypatch.setenv("QWEN_CHAT_MODELS", "qwen3.7-plus,qwen3.7-max")
    monkeypatch.setenv("OBJECT_STORE_BACKEND", "local")
    monkeypatch.setenv("OBJECT_STORE_LOCAL_ROOT", str(unused_local_root))
    get_settings.cache_clear()

    from app import main as main_module

    monkeypatch.setattr("app.services.model_service.OpenAI", FakeOpenAIClient)
    monkeypatch.setattr("app.services.embedding_service.OpenAIEmbeddings", FakeOpenAIEmbeddings)
    monkeypatch.setattr(
        main_module,
        "build_object_store",
        lambda _settings: fake_object_store,
    )
    try:
        engine = create_engine_for_settings(get_settings())
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            engine.dispose()
        app = main_module.create_app(
            knowledge_vector_store_override=fake_knowledge_vector_store,
            start_background_workers=False,
        )
        with TestClient(app) as test_client:
            yield test_client
    finally:
        assert not any(path.is_file() for path in unused_local_root.rglob("*"))
        assert {
            (
                path.relative_to(default_local_root),
                path.stat().st_size,
                path.stat().st_mtime_ns,
            )
            for path in default_local_root.rglob("*")
            if path.is_file()
        } == default_files_before
        get_settings.cache_clear()


@pytest.fixture
def session_factory(client):
    return client.app.state.session_factory


@pytest.fixture
def fake_object_store(client) -> FakeObjectStore:
    store = client.app.state.object_store
    assert isinstance(store, FakeObjectStore)
    return store


@pytest.fixture
def admin_headers(client) -> dict[str, str]:
    response = client.post(
        "/api/auth/admin-password/setup",
        json={"password": "correct horse battery staple"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def create_user(client, admin_headers):
    def factory(
        username: str = "alice",
        password: str = "correct horse battery staple",
    ) -> tuple[dict[str, object], dict[str, str]]:
        created = client.post(
            "/api/admin/users",
            headers=admin_headers,
            json={
                "username": username,
                "display_name": username.title(),
                "password": password,
            },
        )
        assert created.status_code == 201
        login = client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
        )
        assert login.status_code == 200
        return created.json(), {
            "Authorization": f"Bearer {login.json()['access_token']}"
        }

    return factory


@pytest.fixture
def ordinary_user_headers(create_user) -> dict[str, str]:
    _user, headers = create_user()
    return headers
