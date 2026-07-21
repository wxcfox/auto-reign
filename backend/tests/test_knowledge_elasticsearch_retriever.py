from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.repositories.vector_store import VectorStoreUnavailable
from app.services.knowledge_chunk_service import KnowledgeChunk
from app.services.knowledge_retrievers import (
    DocumentGeneration,
    DocumentIndexScope,
    KnowledgeRetrieverHit,
)
from app.services.knowledge_retrievers.elasticsearch import ElasticsearchRetriever
from tests.fakes import StableTestEmbeddings


def settings():
    return SimpleNamespace(
        elasticsearch_url="http://elasticsearch.test:9200",
        elasticsearch_index="knowledge-test",
        elasticsearch_username=None,
        elasticsearch_password=None,
        elasticsearch_api_key=None,
        elasticsearch_request_timeout_seconds=3.0,
    )


class FakeIndices:
    def __init__(self, owner) -> None:
        self.owner = owner

    def exists(self, *, index: str) -> bool:
        assert index == "knowledge-test"
        return self.owner.exists

    def create(self, *, index: str, mappings: dict[str, object]) -> None:
        self.owner.exists = True
        self.owner.created = (index, mappings)


class FakeElasticsearch:
    def __init__(self) -> None:
        self.exists = False
        self.created: tuple[str, dict[str, object]] | None = None
        self.indices = FakeIndices(self)
        self.responses: list[dict[str, object]] = []
        self.search_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.ping_result = True

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.responses.pop(0)

    def delete_by_query(self, **kwargs):
        self.delete_calls.append(kwargs)
        return {"deleted": 1}

    def ping(self) -> bool:
        return self.ping_result


def generation() -> DocumentGeneration:
    return DocumentGeneration(
        collection_id="collection-1",
        owner_user_id=7,
        document_id="doc-1",
        index_generation=2,
        content_hash="sha256-current",
    )


def metadata(*, chunk_index: int = 0) -> dict[str, object]:
    return {
        "owner_user_id": 7,
        "collection_id": "collection-1",
        "document_id": "doc-1",
        "index_generation": 2,
        "content_hash": "sha256-current",
        "filename": "guide.md",
        "chunk_index": chunk_index,
        "source_start": chunk_index * 10,
        "source_end": chunk_index * 10 + 10,
    }


def response(*hits: tuple[str, float, dict[str, object]]) -> dict[str, object]:
    return {
        "hits": {
            "hits": [
                {
                    "_score": score,
                    "_source": {"content": content, "metadata": item_metadata},
                }
                for content, score, item_metadata in hits
            ]
        }
    }


def retriever(client: FakeElasticsearch) -> ElasticsearchRetriever:
    return ElasticsearchRetriever(
        settings=settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=StableTestEmbeddings(),
    )


def test_indexes_content_vector_and_complete_metadata(monkeypatch) -> None:
    client = FakeElasticsearch()
    backend = retriever(client)
    actions: list[dict[str, object]] = []

    def fake_bulk(_client, bulk_actions, *, refresh: str):
        assert refresh == "wait_for"
        actions.extend(bulk_actions)
        return (len(actions), [])

    monkeypatch.setattr(
        "app.services.knowledge_retrievers.elasticsearch.helpers.bulk",
        fake_bulk,
    )
    chunk = KnowledgeChunk(content="authoritative", metadata=metadata())

    backend.upsert_generation([chunk])

    assert client.created is not None
    mapping = client.created[1]["properties"]
    assert mapping["content"]["type"] == "text"  # type: ignore[index]
    assert mapping["embedding"]["type"] == "dense_vector"  # type: ignore[index]
    assert actions[0]["_source"]["content"] == "authoritative"  # type: ignore[index]
    assert actions[0]["_source"]["metadata"] == metadata()  # type: ignore[index]


def test_vector_search_uses_exact_generation_scope_and_returns_vector_score() -> None:
    client = FakeElasticsearch()
    client.exists = True
    client.responses = [response(("vector hit", 0.85, metadata()))]
    backend = retriever(client)

    hits = backend.retrieve(
        "cache",
        scopes=[generation()],
        mode="vector",
        limit=5,
        vector_weight=0.7,
        keyword_weight=0.3,
    )

    assert hits[0].score == pytest.approx(0.85)
    assert hits[0].vector_score == pytest.approx(0.85)
    serialized_query = repr(client.search_calls[0]["query"])
    assert "collection-1" in serialized_query
    assert "sha256-current" in serialized_query
    assert "cosineSimilarity" in serialized_query


def test_keyword_search_normalizes_bm25_by_batch_max() -> None:
    client = FakeElasticsearch()
    client.exists = True
    client.responses = [
        response(
            ("first", 8.0, metadata()),
            ("second", 2.0, metadata(chunk_index=1)),
        )
    ]
    backend = retriever(client)

    hits = backend.retrieve(
        "cache",
        scopes=[generation()],
        mode="keyword",
        limit=5,
        vector_weight=0.7,
        keyword_weight=0.3,
    )

    assert [hit.score for hit in hits] == pytest.approx([1.0, 0.25])
    assert all(hit.retrieval_mode == "keyword" for hit in hits)
    assert "match" in repr(client.search_calls[0]["query"])


def test_hybrid_deduplicates_chunks_and_applies_linear_weights(monkeypatch) -> None:
    client = FakeElasticsearch()
    client.exists = True
    backend = retriever(client)
    vector_hits = [
        KnowledgeRetrieverHit(
            content="shared",
            score=0.8,
            metadata=metadata(),
            retrieval_mode="vector",
            vector_score=0.8,
        ),
        KnowledgeRetrieverHit(
            content="vector only",
            score=0.9,
            metadata=metadata(chunk_index=1),
            retrieval_mode="vector",
            vector_score=0.9,
        ),
    ]
    keyword_hits = [
        KnowledgeRetrieverHit(
            content="shared",
            score=1.0,
            metadata=metadata(),
            retrieval_mode="keyword",
            keyword_score=1.0,
        )
    ]
    monkeypatch.setattr(backend, "_vector_search", lambda *_args, **_kwargs: vector_hits)
    monkeypatch.setattr(backend, "_keyword_search", lambda *_args, **_kwargs: keyword_hits)

    hits = backend.retrieve(
        "cache",
        scopes=[generation()],
        mode="hybrid",
        limit=5,
        vector_weight=0.7,
        keyword_weight=0.3,
    )

    assert len(hits) == 2
    assert hits[0].content == "shared"
    assert hits[0].vector_score == pytest.approx(0.8)
    assert hits[0].keyword_score == pytest.approx(1.0)
    assert hits[0].fused_score == pytest.approx(0.86)
    assert hits[1].fused_score == pytest.approx(0.63)


def test_missing_index_and_connection_failure_are_explicit() -> None:
    client = FakeElasticsearch()
    backend = retriever(client)
    with pytest.raises(VectorStoreUnavailable, match="index is unavailable"):
        backend.retrieve(
            "cache",
            scopes=[generation()],
            mode="vector",
            limit=5,
            vector_weight=0.7,
            keyword_weight=0.3,
        )
    client.ping_result = False
    assert backend.test_connection() is False


def test_delete_generation_uses_all_authoritative_identity_fields() -> None:
    client = FakeElasticsearch()
    backend = retriever(client)

    backend.delete_generation(generation())

    serialized_query = repr(client.delete_calls[0]["query"])
    for expected in ("collection-1", "doc-1", "sha256-current", "index_generation"):
        assert expected in serialized_query


def test_delete_document_and_collection_are_tenant_scoped() -> None:
    client = FakeElasticsearch()
    backend = retriever(client)

    backend.delete_document(
        DocumentIndexScope(
            collection_id="collection-1",
            owner_user_id=7,
            document_id="doc-1",
        )
    )
    backend.purge_collection(collection_id="collection-1", owner_user_id=7)

    document_query = repr(client.delete_calls[0]["query"])
    collection_query = repr(client.delete_calls[1]["query"])
    assert all(value in document_query for value in ("collection-1", "doc-1", "7"))
    assert all(value in collection_query for value in ("collection-1", "7"))
    assert "doc-1" not in collection_query
