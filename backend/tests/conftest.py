from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.db.models import Base
from app.db.session import create_engine_for_settings
from app.main import create_app
from app.repositories.qdrant_store import get_qdrant_store
from app.services.workspace_vector_store import get_workspace_vector_store


@pytest.fixture
def client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'app.db'}")
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setenv("QDRANT_COLLECTION", "auto_reign_test")
    monkeypatch.setenv("DETERMINISTIC_MODEL_FALLBACK", "true")
    get_settings.cache_clear()
    get_qdrant_store.cache_clear()
    get_workspace_vector_store.cache_clear()
    try:
        engine = create_engine_for_settings(get_settings())
        try:
            Base.metadata.create_all(bind=engine)
        finally:
            engine.dispose()
        app = create_app()
        with TestClient(app) as test_client:
            yield test_client
    finally:
        get_qdrant_store.cache_clear()
        get_workspace_vector_store.cache_clear()
        get_settings.cache_clear()
