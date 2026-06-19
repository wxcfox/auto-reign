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
