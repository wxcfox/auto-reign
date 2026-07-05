import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.services.embedding_service import EmbeddingService


class FakeEmbeddings:
    def __init__(self, dimension: int = 32) -> None:
        self.dimension = dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        value = float(len(text))
        return [value, 0.0]


def test_embedding_service_uses_injected_test_double(tmp_path) -> None:
    embeddings = FakeEmbeddings()
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
    )
    service = EmbeddingService(settings=settings, embeddings=embeddings)

    assert service.embed_documents(["one", "two"]) == [[3.0, 0.0], [3.0, 0.0]]
    assert service.embed_query("three") == [5.0, 0.0]


def test_embedding_service_configures_qwen_openai_compatible_client(monkeypatch, tmp_path) -> None:
    calls: list[dict[str, object]] = []

    class FakeOpenAIEmbeddings:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def embed_documents(self, texts):
            return [[float(index), 0.0] for index, _ in enumerate(texts)]

        def embed_query(self, text):
            return [1.0, 0.0]

    monkeypatch.setattr(
        "app.services.embedding_service.OpenAIEmbeddings",
        FakeOpenAIEmbeddings,
    )
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        qwen_api_key="qwen-secret",
        qwen_base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    )

    service = EmbeddingService(settings=settings)

    assert service.embed_query("redis") == [1.0, 0.0]
    assert calls == [
        {
            "model": "text-embedding-v4",
            "api_key": "qwen-secret",
            "base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "check_embedding_ctx_length": False,
            "model_kwargs": {"encoding_format": "float"},
        }
    ]


def test_embedding_service_requires_configured_provider(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        qdrant_url=":memory:",
        embedding_provider="qwen",
        qwen_api_key=None,
    )
    service = EmbeddingService(settings=settings)

    with pytest.raises(HTTPException) as exc_info:
        service.embed_query("redis")

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail["code"] == "embedding_provider_not_configured"
