from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import Distance, QueryResponse, ScoredPoint, VectorParams

from app.core.config import get_settings
from app.repositories import qdrant_store as qdrant_store_module
from app.repositories.qdrant_store import QdrantVectorStore, get_qdrant_store
from app.repositories.vector_store import (
    VectorChunk,
    VectorDimensionMismatch,
    VectorSearchHit,
    VectorStoreUnavailable,
    stable_vector_id,
)


class FakeQdrantClient:
    def __init__(
        self,
        *,
        collection_exists: bool = False,
        dimension: int | None = None,
        fail_on: str | None = None,
        failure: Exception | None = None,
    ) -> None:
        self.has_collection = collection_exists
        self.dimension = dimension
        self.fail_on = fail_on
        self.failure = failure or RuntimeError("Qdrant client failed")
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.query_response = QueryResponse(points=[])

    def collection_exists(self, **kwargs: Any) -> bool:
        self._record("collection_exists", kwargs)
        return self.has_collection

    def get_collection(self, **kwargs: Any) -> SimpleNamespace:
        self._record("get_collection", kwargs)
        vectors = VectorParams(size=self.dimension, distance=Distance.COSINE)
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=vectors)))

    def create_collection(self, **kwargs: Any) -> bool:
        self._record("create_collection", kwargs)
        self.has_collection = True
        self.dimension = kwargs["vectors_config"].size
        return True

    def upsert(self, **kwargs: Any) -> None:
        self._record("upsert", kwargs)

    def delete(self, **kwargs: Any) -> None:
        self._record("delete", kwargs)

    def query_points(self, **kwargs: Any) -> QueryResponse:
        self._record("query_points", kwargs)
        return self.query_response

    def _record(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        if self.fail_on == operation:
            raise self.failure


def make_chunk(
    *,
    chunk_id: str = "7d8a1b7d-50d0-52cd-95e0-f559b4496a87",
    content: str = "Python service design",
    embedding: list[float] | None = None,
    metadata: dict[str, str | int | float | bool] | None = None,
) -> VectorChunk:
    return VectorChunk(
        id=chunk_id,
        content=content,
        embedding=embedding if embedding is not None else [1.0, 0.0],
        metadata=metadata if metadata is not None else {"document_id": "document-1"},
    )


def test_vector_value_objects_are_frozen() -> None:
    chunk = VectorChunk(
        id="chunk-id",
        content="content",
        embedding=[1.0],
        metadata={"document_id": "document-1"},
    )
    hit = VectorSearchHit(content="content", score=0.9, metadata={"kind": "resume"})

    with pytest.raises(FrozenInstanceError):
        chunk.content = "changed"
    with pytest.raises(FrozenInstanceError):
        hit.score = 0.1


def test_stable_vector_id_is_deterministic_and_parseable() -> None:
    first = stable_vector_id("document", "document-1", 0)
    second = stable_vector_id("document", "document-1", 0)

    assert first == second
    assert first == str(uuid5(NAMESPACE_URL, "auto-reign:document:document-1:0"))
    assert str(UUID(first)) == first


def test_stable_vector_id_changes_with_source_or_chunk_index() -> None:
    ids = {
        stable_vector_id("document", "document-1", 0),
        stable_vector_id("memory", "document-1", 0),
        stable_vector_id("document", "document-2", 0),
        stable_vector_id("document", "document-1", 1),
    }

    assert len(ids) == 4


def test_empty_upsert_is_a_no_op() -> None:
    client = FakeQdrantClient()

    QdrantVectorStore(client).upsert_chunks("documents", [])

    assert client.calls == []


def test_upsert_lazily_creates_cosine_collection_and_preserves_metadata() -> None:
    client = FakeQdrantClient()
    metadata = {"document_id": "document-1", "chunk_index": 0}
    chunk = make_chunk(metadata=metadata)

    QdrantVectorStore(client).upsert_chunks("documents", [chunk])

    assert [name for name, _ in client.calls] == [
        "collection_exists",
        "create_collection",
        "upsert",
    ]
    create_call = client.calls[1][1]
    assert create_call["collection_name"] == "documents"
    assert create_call["vectors_config"].size == 2
    assert create_call["vectors_config"].distance == Distance.COSINE
    upsert_call = client.calls[2][1]
    assert upsert_call["collection_name"] == "documents"
    assert upsert_call["wait"] is True
    assert len(upsert_call["points"]) == 1
    point = upsert_call["points"][0]
    assert point.id == chunk.id
    assert point.vector == chunk.embedding
    assert point.payload == {**metadata, "content": chunk.content}
    assert metadata == {"document_id": "document-1", "chunk_index": 0}


def test_upsert_reuses_existing_collection_with_matching_dimension() -> None:
    client = FakeQdrantClient(collection_exists=True, dimension=2)

    QdrantVectorStore(client).upsert_chunks("documents", [make_chunk()])

    assert [name for name, _ in client.calls] == [
        "collection_exists",
        "get_collection",
        "upsert",
    ]


@pytest.mark.parametrize(
    "chunks",
    [
        [make_chunk(embedding=[])],
        [make_chunk(embedding=[1.0, 0.0]), make_chunk(embedding=[1.0])],
    ],
    ids=["empty-embedding", "mixed-dimensions"],
)
def test_invalid_chunk_dimensions_fail_before_client_access(chunks: list[VectorChunk]) -> None:
    client = FakeQdrantClient()

    with pytest.raises(VectorDimensionMismatch):
        QdrantVectorStore(client).upsert_chunks("documents", chunks)

    assert client.calls == []


def test_existing_collection_dimension_mismatch_fails_without_upsert() -> None:
    client = FakeQdrantClient(collection_exists=True, dimension=3)

    with pytest.raises(VectorDimensionMismatch):
        QdrantVectorStore(client).upsert_chunks("documents", [make_chunk()])

    assert [name for name, _ in client.calls] == [
        "collection_exists",
        "get_collection",
    ]


def test_delete_document_chunks_is_a_no_op_when_collection_is_absent() -> None:
    client = FakeQdrantClient()

    QdrantVectorStore(client).delete_document_chunks("documents", "document-1")

    assert client.calls == [("collection_exists", {"collection_name": "documents"})]


def test_delete_document_chunks_uses_document_id_filter_and_waits() -> None:
    client = FakeQdrantClient(collection_exists=True, dimension=2)

    QdrantVectorStore(client).delete_document_chunks("documents", "document-1")

    assert [name for name, _ in client.calls] == ["collection_exists", "delete"]
    delete_call = client.calls[1][1]
    assert delete_call["collection_name"] == "documents"
    assert delete_call["wait"] is True
    selector = delete_call["points_selector"]
    assert selector.filter.must is not None
    assert len(selector.filter.must) == 1
    condition = selector.filter.must[0]
    assert condition.key == "document_id"
    assert condition.match.value == "document-1"


def test_search_is_empty_when_collection_is_absent() -> None:
    client = FakeQdrantClient()

    hits = QdrantVectorStore(client).search("documents", [1.0, 0.0], 5)

    assert hits == []
    assert client.calls == [("collection_exists", {"collection_name": "documents"})]


def test_search_maps_query_arguments_and_hits_without_mutating_payload() -> None:
    client = FakeQdrantClient(collection_exists=True, dimension=2)
    point = ScoredPoint(
        id="7d8a1b7d-50d0-52cd-95e0-f559b4496a87",
        version=1,
        score=0.92,
        payload={
            "content": "Python service design",
            "document_id": "document-1",
            "chunk_index": 0,
        },
    )
    client.query_response = QueryResponse(points=[point])

    hits = QdrantVectorStore(client).search("documents", [1.0, 0.0], 3)

    assert [name for name, _ in client.calls] == [
        "collection_exists",
        "get_collection",
        "query_points",
    ]
    assert client.calls[2][1] == {
        "collection_name": "documents",
        "query": [1.0, 0.0],
        "limit": 3,
        "with_payload": True,
    }
    assert hits == [
        VectorSearchHit(
            content="Python service design",
            score=0.92,
            metadata={"document_id": "document-1", "chunk_index": 0},
        )
    ]
    assert point.payload == {
        "content": "Python service design",
        "document_id": "document-1",
        "chunk_index": 0,
    }


def test_search_dimension_mismatch_fails_before_query() -> None:
    client = FakeQdrantClient(collection_exists=True, dimension=3)

    with pytest.raises(VectorDimensionMismatch):
        QdrantVectorStore(client).search("documents", [1.0, 0.0], 3)

    assert [name for name, _ in client.calls] == [
        "collection_exists",
        "get_collection",
    ]


@pytest.mark.parametrize(
    "operation",
    [
        "collection_exists",
        "get_collection",
        "create_collection",
        "upsert",
        "delete",
        "query_points",
    ],
)
def test_client_failures_map_to_unavailable_with_original_cause(operation: str) -> None:
    failure = RuntimeError(f"{operation} failed")
    collection_exists = operation != "create_collection"
    client = FakeQdrantClient(
        collection_exists=collection_exists,
        dimension=2,
        fail_on=operation,
        failure=failure,
    )
    store = QdrantVectorStore(client)

    with pytest.raises(VectorStoreUnavailable) as exc_info:
        if operation in {"create_collection", "upsert"}:
            store.upsert_chunks("documents", [make_chunk()])
        elif operation == "delete":
            store.delete_document_chunks("documents", "document-1")
        else:
            store.search("documents", [1.0, 0.0], 3)

    assert exc_info.value.__cause__ is failure


def test_vector_store_errors_from_client_are_reraised_unchanged() -> None:
    failure = VectorDimensionMismatch("dimension conflict")
    client = FakeQdrantClient(
        collection_exists=True,
        dimension=2,
        fail_on="collection_exists",
        failure=failure,
    )

    with pytest.raises(VectorDimensionMismatch) as exc_info:
        QdrantVectorStore(client).search("documents", [1.0, 0.0], 3)

    assert exc_info.value is failure


def test_get_qdrant_store_caches_memory_client_until_cache_clear(monkeypatch, tmp_path) -> None:
    clients: list[FakeQdrantClient] = []
    constructor_calls: list[dict[str, Any]] = []

    def make_client(**kwargs: Any) -> FakeQdrantClient:
        constructor_calls.append(kwargs)
        client = FakeQdrantClient()
        clients.append(client)
        return client

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setattr(qdrant_store_module, "QdrantClient", make_client)
    get_settings.cache_clear()
    get_qdrant_store.cache_clear()
    try:
        first = get_qdrant_store()
        second = get_qdrant_store()

        assert first is second
        assert constructor_calls == [{"location": ":memory:"}]

        get_qdrant_store.cache_clear()
        third = get_qdrant_store()

        assert third is not first
        assert constructor_calls == [
            {"location": ":memory:"},
            {"location": ":memory:"},
        ]
        assert len(clients) == 2
    finally:
        get_qdrant_store.cache_clear()
        get_settings.cache_clear()


def test_get_qdrant_store_uses_url_for_remote_client(monkeypatch, tmp_path) -> None:
    constructor_calls: list[dict[str, Any]] = []

    def make_client(**kwargs: Any) -> FakeQdrantClient:
        constructor_calls.append(kwargs)
        return FakeQdrantClient()

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.example:6333")
    monkeypatch.setattr(qdrant_store_module, "QdrantClient", make_client)
    get_settings.cache_clear()
    get_qdrant_store.cache_clear()
    try:
        get_qdrant_store()

        assert constructor_calls == [{"url": "http://qdrant.example:6333"}]
    finally:
        get_qdrant_store.cache_clear()
        get_settings.cache_clear()


def test_get_qdrant_store_maps_client_construction_failure(monkeypatch, tmp_path) -> None:
    failure = RuntimeError("client construction failed")

    def fail_client(**_kwargs: Any) -> FakeQdrantClient:
        raise failure

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("QDRANT_URL", ":memory:")
    monkeypatch.setattr(qdrant_store_module, "QdrantClient", fail_client)
    get_settings.cache_clear()
    get_qdrant_store.cache_clear()
    try:
        with pytest.raises(VectorStoreUnavailable) as exc_info:
            get_qdrant_store()

        assert exc_info.value.__cause__ is failure
    finally:
        get_qdrant_store.cache_clear()
        get_settings.cache_clear()


def test_real_memory_client_upserts_searches_and_deletes_document_chunks() -> None:
    client = QdrantClient(location=":memory:")
    store = QdrantVectorStore(client)
    try:
        store.upsert_chunks(
            "documents",
            [
                make_chunk(
                    chunk_id=stable_vector_id("document", "document-1", 0),
                    content="Python service design",
                    embedding=[1.0, 0.0],
                    metadata={"document_id": "document-1", "chunk_index": 0},
                ),
                make_chunk(
                    chunk_id=stable_vector_id("document", "document-2", 0),
                    content="Database indexing",
                    embedding=[0.0, 1.0],
                    metadata={"document_id": "document-2", "chunk_index": 0},
                ),
            ],
        )

        hits = store.search("documents", [1.0, 0.0], 2)

        assert [hit.content for hit in hits] == [
            "Python service design",
            "Database indexing",
        ]
        assert hits[0].metadata == {"document_id": "document-1", "chunk_index": 0}

        store.delete_document_chunks("documents", "document-1")

        remaining_hits = store.search("documents", [1.0, 0.0], 2)
        assert [hit.content for hit in remaining_hits] == ["Database indexing"]
    finally:
        client.close()
