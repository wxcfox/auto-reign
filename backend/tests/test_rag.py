from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from fastapi import HTTPException
from app.repositories.vector_store import VectorChunk, VectorStoreUnavailable
from app.repositories.qdrant_store import QdrantVectorStore
from app.services.rag_service import RagService


def test_uploaded_document_is_searchable(client: TestClient) -> None:
    upload = client.post(
        "/api/documents/upload",
        files={
            "file": (
                "notes.txt",
                b"FastAPI dependency injection and Chroma retrieval notes.",
                "text/plain",
            )
        },
    )
    assert upload.status_code == 200
    search = client.post("/api/rag/search", json={"query": "Chroma retrieval", "limit": 3})
    assert search.status_code == 200
    hits = search.json()["hits"]
    assert hits
    assert hits[0]["source_type"] == "document"


@pytest.mark.parametrize(
    ("settings_overrides", "expected_model", "expected_base_url"),
    [
            (
                {
                    "openai_api_key": "openai-secret",
                    "embedding_provider": "openai",
                    "embedding_model": "text-embedding-3-small",
                },
                "text-embedding-3-small",
                None,
            ),
        (
            {
                "qwen_api_key": "qwen-secret",
                "embedding_provider": "qwen",
                "embedding_model": "text-embedding-v4",
                "qwen_base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            },
            "text-embedding-v4",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        ),
    ],
)
def test_embed_texts_uses_supported_provider_when_configured(
    tmp_path, settings_overrides: dict[str, str], expected_model: str, expected_base_url: str | None
) -> None:
    calls: list[dict[str, object]] = []

    class FakeEmbeddings:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(embedding=[0.1, 0.2]),
                    SimpleNamespace(embedding=[0.3, 0.4]),
                ]
            )

    factory_calls: list[dict[str, str | None]] = []

    def client_factory(*, api_key: str, base_url: str | None = None):
        factory_calls.append({"api_key": api_key, "base_url": base_url})
        return SimpleNamespace(embeddings=FakeEmbeddings())

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        deterministic_model_fallback=False,
        **settings_overrides,
    )
    service = RagService(
        settings=settings,
        embedding_client=None,
        embedding_client_factory=client_factory,
    )

    embeddings = service.embed_texts(["first", "second"])

    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    assert factory_calls == [
        {
            "api_key": settings_overrides.get("openai_api_key")
            or settings_overrides.get("qwen_api_key"),
            "base_url": expected_base_url,
        }
    ]
    assert calls == [
        {
            "input": ["first", "second"],
            "model": expected_model,
            "encoding_format": "float",
        }
    ]


def test_search_uses_configured_embedding_client(tmp_path) -> None:
    embedding_calls: list[dict[str, object]] = []
    search_calls: list[dict[str, object]] = []

    class FakeEmbeddings:
        def create(self, **kwargs):
            embedding_calls.append(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(embedding=[0.5, 0.25])])

    class FakeVectorStore:
        def has_searchable_content(self, collection_name):
            return True

        def search(self, collection_name, query_embedding, limit):
            search_calls.append(
                {
                    "collection_name": collection_name,
                    "query_embedding": query_embedding,
                    "limit": limit,
                }
            )
            return []

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        openai_api_key="openai-secret",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        deterministic_model_fallback=False,
    )
    service = RagService(
        settings=settings,
        embedding_client=SimpleNamespace(embeddings=FakeEmbeddings()),
        vector_store=FakeVectorStore(),
    )

    assert service.search(None, "retrieval query", 3) == []
    assert embedding_calls == [
        {
            "input": ["retrieval query"],
            "model": "text-embedding-3-small",
            "encoding_format": "float",
        }
    ]
    assert search_calls == [
        {
            "collection_name": "auto_reign_test",
            "query_embedding": [0.5, 0.25],
            "limit": 3,
        }
    ]


def test_search_returns_empty_without_embeddings_when_collection_has_no_content(tmp_path) -> None:
    class FakeEmbeddings:
        def create(self, **_kwargs):
            raise AssertionError("embeddings should not run for an empty collection")

    class FakeVectorStore:
        def has_searchable_content(self, collection_name):
            assert collection_name == "auto_reign_test"
            return False

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        openai_api_key="openai-secret",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        deterministic_model_fallback=False,
    )
    service = RagService(
        settings=settings,
        embedding_client=SimpleNamespace(embeddings=FakeEmbeddings()),
        vector_store=FakeVectorStore(),
    )

    assert service.search(None, "retrieval query", 3) == []


def test_index_failure_is_committed_as_failed(client: TestClient, monkeypatch) -> None:
    def fail_upsert(self, collection_name, chunks):
        raise VectorStoreUnavailable("qdrant unavailable")

    monkeypatch.setattr(QdrantVectorStore, "upsert_chunks", fail_upsert)

    response = client.post(
        "/api/documents/upload",
        files={"file": ("notes.txt", b"RAG notes", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["index_status"] == "failed"
    persisted = client.get(f"/api/documents/{body['id']}")
    assert persisted.status_code == 200
    assert persisted.json()["index_status"] == "failed"


def test_search_surfaces_vector_store_unavailability(client: TestClient, monkeypatch) -> None:
    def has_searchable_content(self, collection_name):
        return True

    def fail_search(self, collection_name, query_embedding, limit):
        raise VectorStoreUnavailable("qdrant unavailable")

    monkeypatch.setattr(QdrantVectorStore, "has_searchable_content", has_searchable_content)
    monkeypatch.setattr(QdrantVectorStore, "search", fail_search)

    response = client.post("/api/rag/search", json={"query": "retrieval query", "limit": 3})

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "vector_store_unavailable"


def test_indexed_document_uses_stable_uuid_vector_ids(tmp_path) -> None:
    upsert_calls: list[tuple[str, list[VectorChunk]]] = []

    class FakeVectorStore:
        def has_searchable_content(self, collection_name):
            return True

        def delete_document_chunks(self, collection_name, document_id):
            return None

        def upsert_chunks(self, collection_name, chunks):
            upsert_calls.append((collection_name, chunks))

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        deterministic_model_fallback=True,
    )
    document = SimpleNamespace(
        id="document-1",
        collection="auto_reign_test",
        title="Resume",
        tags=["python"],
        file_path=str(tmp_path / "resume.txt"),
        index_status="pending",
    )
    session = SimpleNamespace(flush=lambda: None)
    (tmp_path / "resume.txt").write_text("Python service design", encoding="utf-8")
    service = RagService(
        settings=settings,
        vector_store=FakeVectorStore(),
        chunk_repository=SimpleNamespace(delete_for_document=lambda *_args: None, add_many=lambda *_args: None),
    )

    service.index_document(session, document)

    assert upsert_calls
    _, chunks = upsert_calls[0]
    assert chunks
    for chunk in chunks:
        assert str(UUID(chunk.id)) == chunk.id


def test_embed_texts_logs_provider_error_details(tmp_path, caplog: pytest.LogCaptureFixture) -> None:
    class FailingEmbeddings:
        def create(self, **_kwargs):
            raise RuntimeError("quota exceeded for project")

    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        qdrant_collection="auto_reign_test",
        openai_api_key="openai-secret",
        embedding_provider="openai",
        embedding_model="text-embedding-3-small",
        deterministic_model_fallback=False,
    )
    service = RagService(
        settings=settings,
        embedding_client=SimpleNamespace(embeddings=FailingEmbeddings()),
    )

    with caplog.at_level("ERROR"):
        with pytest.raises(HTTPException) as error:
            service.embed_texts(["hello"])

    record = caplog.records[-1]
    assert error.value.status_code == 502
    assert error.value.detail["code"] == "embedding_call_failed"
    assert "provider=openai" in caplog.text
    assert "model=text-embedding-3-small" in caplog.text
    assert "quota exceeded for project" in caplog.text
    assert "RuntimeError" in caplog.text
    assert record.provider == "openai"
    assert record.model == "text-embedding-3-small"
    assert record.error_type == "RuntimeError"
    assert record.error_message == "quota exceeded for project"
