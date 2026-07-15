from types import SimpleNamespace

import pytest
import httpx
from openai import APIStatusError

from app.core.config import Settings
from app.services.embedding_service import (
    EmbeddingProviderError,
    SingleRequestEmbeddings,
)


def test_embedding_documents_send_one_text_per_request(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class Embeddings:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[float(len(calls))])]
            )

    class Client:
        def __init__(self, **_kwargs):
            self.embeddings = Embeddings()

    monkeypatch.setattr("app.services.embedding_service.OpenAI", Client)
    embedding = SingleRequestEmbeddings(
        model="text-embedding-v4",
        api_key="test-key",
        base_url="https://example.test/v1",
    )

    assert embedding.embed_documents(["one", "two"]) == [[1.0], [2.0]]
    assert [call["input"] for call in calls] == ["one", "two"]


def test_embedding_retries_retryable_status(monkeypatch) -> None:
    calls = 0

    class Embeddings:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                raise APIStatusError(
                    "temporary failure",
                    response=httpx.Response(503, request=httpx.Request("POST", "https://example.test")),
                    body=None,
                )
            return SimpleNamespace(data=[SimpleNamespace(embedding=[1.0])])

    class Client:
        def __init__(self, **_kwargs):
            self.embeddings = Embeddings()

    monkeypatch.setattr("app.services.embedding_service.OpenAI", Client)
    monkeypatch.setattr("app.services.embedding_service.time.sleep", lambda _seconds: None)
    embedding = SingleRequestEmbeddings(
        model="text-embedding-v4",
        api_key="test-key",
        base_url="https://example.test/v1",
    )

    assert embedding.embed_query("query") == [1.0]
    assert calls == 3


def test_embedding_does_not_retry_non_retryable_status(monkeypatch) -> None:
    calls = 0

    class Embeddings:
        def create(self, **_kwargs):
            nonlocal calls
            calls += 1
            raise APIStatusError(
                "invalid request",
                response=httpx.Response(400, request=httpx.Request("POST", "https://example.test")),
                body=None,
            )

    class Client:
        def __init__(self, **_kwargs):
            self.embeddings = Embeddings()

    monkeypatch.setattr("app.services.embedding_service.OpenAI", Client)
    embedding = SingleRequestEmbeddings(
        model="text-embedding-v4",
        api_key="test-key",
        base_url="https://example.test/v1",
    )

    with pytest.raises(EmbeddingProviderError) as error:
        embedding.embed_query("query")
    assert calls == 1
    assert error.value.code == "embedding_invalid_request"


def test_settings_keep_qwen_embedding_configuration() -> None:
    settings = Settings(
        embedding_provider="qwen",
        embedding_model="text-embedding-v4",
        qwen_api_key="test-key",
    )
    assert settings.embedding_provider == "qwen"
    assert settings.embedding_model == "text-embedding-v4"
