from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import Settings
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


def test_embed_texts_uses_openai_when_configured(tmp_path) -> None:
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

    client = SimpleNamespace(embeddings=FakeEmbeddings())
    settings = Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "app.db",
        chroma_dir=tmp_path / "chroma",
        openai_api_key="openai-secret",
        deterministic_model_fallback=False,
    )
    service = RagService(settings=settings, embedding_client=client)

    embeddings = service.embed_texts(["first", "second"])

    assert embeddings == [[0.1, 0.2], [0.3, 0.4]]
    assert calls == [
        {
            "input": ["first", "second"],
            "model": "text-embedding-3-small",
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

    class FakeChromaStore:
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
        sqlite_path=tmp_path / "app.db",
        chroma_dir=tmp_path / "chroma",
        openai_api_key="openai-secret",
        deterministic_model_fallback=False,
    )
    service = RagService(
        settings=settings,
        embedding_client=SimpleNamespace(embeddings=FakeEmbeddings()),
        chroma_store=FakeChromaStore(),
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
            "collection_name": "default",
            "query_embedding": [0.5, 0.25],
            "limit": 3,
        }
    ]
