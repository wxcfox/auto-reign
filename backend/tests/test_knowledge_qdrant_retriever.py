from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

from langchain_core.documents import Document
import pytest
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition

from app.repositories.vector_store import VectorStoreUnavailable, stable_vector_id
from app.services.knowledge_chunk_service import KnowledgeChunk, KnowledgeChunkService
from app.services.knowledge_retrievers import (
    DocumentGeneration,
    DocumentIndexScope as DocumentVectorScope,
)
from app.services.knowledge_retrievers.qdrant import (
    QdrantRetriever,
    build_knowledge_embeddings,
    build_qdrant_client,
)
from tests.fakes import StableTestEmbeddings


def vector_settings():
    return SimpleNamespace(
        qdrant_collection="knowledge-test",
        qdrant_url=":memory:",
    )


def chunks_for(
    document_id: str,
    *,
    generation: int,
    text: str,
    collection_id: str = "collection-1",
    owner_user_id: int = 7,
    content_hash: str | None = None,
    filename: str = "guide.md",
    chunk_index: int = 0,
) -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            content=text,
            metadata={
                "collection_id": collection_id,
                "owner_user_id": owner_user_id,
                "document_id": document_id,
                "index_generation": generation,
                "content_hash": content_hash or f"sha256-{generation}",
                "filename": filename,
                "chunk_index": chunk_index,
                "source_start": 0,
                "source_end": len(text),
            },
        )
    ]


@pytest.fixture
def qdrant_client() -> Iterator[QdrantClient]:
    client = QdrantClient(location=":memory:")
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def store(qdrant_client: QdrantClient) -> QdrantRetriever:
    return QdrantRetriever(
        settings=vector_settings(),
        client=qdrant_client,
        embeddings=StableTestEmbeddings(),
    )


def current_scope(**overrides: object) -> DocumentGeneration:
    values: dict[str, object] = {
        "collection_id": "collection-1",
        "owner_user_id": 7,
        "document_id": "doc-1",
        "index_generation": 2,
        "content_hash": "sha256-current",
    }
    values.update(overrides)
    return DocumentGeneration(**values)  # type: ignore[arg-type]


def test_point_ids_differ_between_generations() -> None:
    assert stable_vector_id("knowledge", "doc-1", 1, 0) != stable_vector_id(
        "knowledge",
        "doc-1",
        2,
        0,
    )


def test_upsert_writes_generation_metadata_and_stable_point_id(
    store: QdrantRetriever,
    qdrant_client: QdrantClient,
) -> None:
    chunks = chunks_for(
        "doc-1",
        generation=3,
        text="Original source chunk",
        content_hash="sha256-source",
    )
    store.upsert_generation(chunks)

    points, _ = qdrant_client.scroll(
        collection_name=store.collection_name,
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert len(points) == 1
    assert str(points[0].id) == stable_vector_id("knowledge", "doc-1", 3, 0)
    assert points[0].payload == {
        "page_content": "Original source chunk",
        "metadata": chunks[0].metadata,
    }


def test_sparse_source_skips_blank_slices_and_upserts_to_real_qdrant(
    store: QdrantRetriever,
    qdrant_client: QdrantClient,
) -> None:
    text = "x" + (" " * 2_500) + "y"
    chunks = KnowledgeChunkService(chunk_size=200, chunk_overlap=100).split(
        document_id="doc-sparse",
        collection_id="collection-1",
        owner_user_id=7,
        generation=1,
        content_hash="sha256-sparse",
        filename="sparse.txt",
        text=text,
    )

    assert len(chunks) == 2
    assert all(chunk.content.strip() for chunk in chunks)
    assert [chunk.metadata["chunk_index"] for chunk in chunks] == list(
        range(len(chunks))
    )
    assert all(
        chunk.content
        == text[
            chunk.metadata["source_start"] : chunk.metadata["source_end"]
        ]
        for chunk in chunks
    )
    omitted = text[
        chunks[0].metadata["source_end"] : chunks[1].metadata["source_start"]
    ]
    assert omitted
    assert not omitted.strip()

    store.upsert_generation(chunks)

    points, _ = qdrant_client.scroll(
        collection_name=store.collection_name,
        limit=100,
        with_payload=True,
        with_vectors=False,
    )
    assert len(points) == len(chunks)
    assert {point.payload["page_content"] for point in points} == {
        chunk.content for chunk in chunks
    }


def test_search_filters_document_and_current_generation_together(
    store: QdrantRetriever,
) -> None:
    store.upsert_generation(
        chunks_for(
            "doc-1",
            generation=1,
            text="stale",
            content_hash="sha256-stale",
        )
    )
    store.upsert_generation(
        chunks_for(
            "doc-1",
            generation=2,
            text="current",
            content_hash="sha256-current",
        )
    )

    hits = store.search("current", scopes=[current_scope()], limit=5)

    assert [hit.content for hit in hits] == ["current"]
    assert hits[0].metadata["index_generation"] == 2
    assert hits[0].metadata["content_hash"] == "sha256-current"


def test_search_or_conditions_do_not_cross_pair_tenant_or_generation(
    store: QdrantRetriever,
) -> None:
    store.upsert_generation(
        chunks_for(
            "doc-a",
            generation=1,
            text="allowed alpha",
            owner_user_id=7,
            content_hash="hash-a",
        )
    )
    store.upsert_generation(
        chunks_for(
            "doc-b",
            generation=2,
            text="allowed beta",
            owner_user_id=8,
            content_hash="hash-b",
        )
    )
    store.upsert_generation(
        chunks_for(
            "doc-a",
            generation=2,
            text="mixed forbidden",
            owner_user_id=8,
            content_hash="hash-b",
        )
    )
    scopes = [
        current_scope(
            document_id="doc-a",
            owner_user_id=7,
            index_generation=1,
            content_hash="hash-a",
        ),
        current_scope(
            document_id="doc-b",
            owner_user_id=8,
            index_generation=2,
            content_hash="hash-b",
        ),
    ]

    hits = store.search("mixed forbidden", scopes=scopes, limit=10)

    assert {hit.content for hit in hits} == {"allowed alpha", "allowed beta"}
    assert "mixed forbidden" not in {hit.content for hit in hits}


def test_vector_search_fails_closed_for_empty_scopes(
    store: QdrantRetriever,
) -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        store.search("anything", scopes=[], limit=5)


@pytest.mark.parametrize(
    ("query", "limit"),
    [("", 1), ("  ", 1), ("query", 0), ("query", True)],
)
def test_vector_search_rejects_invalid_query_or_limit(
    store: QdrantRetriever,
    query: str,
    limit: int,
) -> None:
    with pytest.raises(ValueError):
        store.search(query, scopes=[current_scope()], limit=limit)


def test_vector_upsert_rejects_malformed_tenant_payload(
    store: QdrantRetriever,
) -> None:
    chunks = chunks_for("doc-1", generation=1, text="source")
    chunks[0].metadata["owner_user_id"] = True

    with pytest.raises(ValueError, match="scope is invalid"):
        store.upsert_generation(chunks)


def test_vector_upsert_rejects_incomplete_metadata(
    store: QdrantRetriever,
) -> None:
    chunk = chunks_for("doc-1", generation=1, text="source")[0]
    del chunk.metadata["content_hash"]

    with pytest.raises(ValueError, match="metadata is incomplete"):
        store.upsert_generation([chunk])


def test_vector_upsert_rejects_duplicate_point_ids(
    store: QdrantRetriever,
) -> None:
    chunk = chunks_for("doc-1", generation=1, text="source")[0]

    with pytest.raises(ValueError, match="must be unique"):
        store.upsert_generation([chunk, chunk])


def condition_keys(conditions: list[Any]) -> set[str]:
    return {
        condition.key
        for condition in conditions
        if isinstance(condition, FieldCondition)
    }


class RecordingDeleteClient:
    def __init__(self, *, fail_delete: bool = False) -> None:
        self.fail_delete = fail_delete
        self.delete_calls: list[tuple[str, object, bool]] = []

    def collection_exists(self, *, collection_name: str) -> bool:
        assert collection_name == "knowledge-test"
        return True

    def delete(self, *, collection_name: str, points_selector, wait: bool):
        if self.fail_delete:
            raise RuntimeError("delete failed")
        self.delete_calls.append((collection_name, points_selector, wait))


def test_vector_mutations_never_use_a_bare_document_id() -> None:
    client = RecordingDeleteClient()
    store = QdrantRetriever(
        settings=vector_settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=StableTestEmbeddings(),
    )
    current = current_scope()

    store.delete_generation(current)
    store.delete_generations_before(current)
    store.delete_document(
        DocumentVectorScope(
            collection_id=current.collection_id,
            owner_user_id=current.owner_user_id,
            document_id=current.document_id,
        )
    )

    assert all(call[0] == "knowledge-test" and call[2] for call in client.delete_calls)
    exact, old, whole = [call[1].filter for call in client.delete_calls]
    assert condition_keys(exact.must) == {
        "metadata.collection_id",
        "metadata.owner_user_id",
        "metadata.document_id",
        "metadata.index_generation",
        "metadata.content_hash",
    }
    assert condition_keys(old.must) == {
        "metadata.collection_id",
        "metadata.owner_user_id",
        "metadata.document_id",
        "metadata.index_generation",
    }
    generation_condition = next(
        condition
        for condition in old.must
        if isinstance(condition, FieldCondition)
        and condition.key == "metadata.index_generation"
    )
    assert generation_condition.range is not None
    assert generation_condition.range.lt == current.index_generation
    assert not old.must_not
    assert condition_keys(whole.must) == {
        "metadata.collection_id",
        "metadata.owner_user_id",
        "metadata.document_id",
    }


def test_delete_generations_before_preserves_current_and_future_generations(
    store: QdrantRetriever,
    qdrant_client: QdrantClient,
) -> None:
    store.upsert_generation(
        chunks_for(
            "doc-1",
            generation=1,
            text="old",
            content_hash="sha256-old",
        )
    )
    store.upsert_generation(
        chunks_for(
            "doc-1",
            generation=2,
            text="current",
            content_hash="sha256-current",
        )
    )
    store.upsert_generation(
        chunks_for(
            "doc-1",
            generation=3,
            text="future",
            content_hash="sha256-future",
        )
    )

    store.delete_generations_before(current_scope())

    points, _ = qdrant_client.scroll(
        collection_name=store.collection_name,
        limit=10,
        with_payload=True,
        with_vectors=False,
    )
    assert sorted(point.payload["page_content"] for point in points) == [
        "current",
        "future",
    ]


class StaticSearchStore:
    def __init__(self, result: tuple[object, object]) -> None:
        self.result = result

    def similarity_search_with_score(self, *_args, **_kwargs):
        return [self.result]


def valid_result_metadata() -> dict[str, object]:
    return {
        "collection_id": "collection-1",
        "owner_user_id": 7,
        "document_id": "doc-1",
        "index_generation": 2,
        "content_hash": "sha256-current",
        "filename": "guide.md",
        "chunk_index": 0,
        "source_start": 0,
        "source_end": 4,
    }


@pytest.mark.parametrize(
    ("raw_cosine", "expected_score"),
    [
        (-1.0, 0.0),
        (0.0, 0.5),
        (1.0, 1.0),
        (1.0 + 5e-7, 1.0),
        (-1.0 - 5e-7, 0.0),
    ],
)
def test_qdrant_cosine_score_is_normalized_to_shared_zero_one_contract(
    monkeypatch: pytest.MonkeyPatch,
    raw_cosine: float,
    expected_score: float,
) -> None:
    client = RecordingDeleteClient()
    store = QdrantRetriever(
        settings=vector_settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=StableTestEmbeddings(),
    )
    document = Document(page_content="text", metadata=valid_result_metadata())
    monkeypatch.setattr(
        store,
        "_store",
        lambda: StaticSearchStore((document, raw_cosine)),
    )

    hits = store.search("query", scopes=[current_scope()], limit=1)

    assert hits[0].score == pytest.approx(expected_score)
    assert hits[0].vector_score == pytest.approx(expected_score)
    assert 0.0 <= hits[0].score <= 1.0


@pytest.mark.parametrize("raw_cosine", [-1.01, 1.01, float("inf"), float("-inf")])
def test_qdrant_rejects_cosine_scores_outside_the_shared_contract(
    monkeypatch: pytest.MonkeyPatch,
    raw_cosine: float,
) -> None:
    client = RecordingDeleteClient()
    store = QdrantRetriever(
        settings=vector_settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=StableTestEmbeddings(),
    )
    document = Document(page_content="text", metadata=valid_result_metadata())
    monkeypatch.setattr(
        store,
        "_store",
        lambda: StaticSearchStore((document, raw_cosine)),
    )

    with pytest.raises(VectorStoreUnavailable, match="normalize search results"):
        store.search("query", scopes=[current_scope()], limit=1)


@pytest.mark.parametrize(
    ("content", "score"),
    [
        (b"bytes", 0.9),
        ("text", float("nan")),
        ("text", True),
        ("text", "0.9"),
    ],
)
def test_vector_adapter_maps_malformed_results_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    content: object,
    score: object,
) -> None:
    client = RecordingDeleteClient()
    store = QdrantRetriever(
        settings=vector_settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=StableTestEmbeddings(),
    )
    document = SimpleNamespace(
        page_content=content,
        metadata=valid_result_metadata(),
    )
    monkeypatch.setattr(store, "_store", lambda: StaticSearchStore((document, score)))

    with pytest.raises(VectorStoreUnavailable):
        store.search("query", scopes=[current_scope()], limit=1)


def test_vector_adapter_rejects_result_outside_requested_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RecordingDeleteClient()
    store = QdrantRetriever(
        settings=vector_settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=StableTestEmbeddings(),
    )
    metadata = valid_result_metadata()
    metadata["owner_user_id"] = 8
    monkeypatch.setattr(
        store,
        "_store",
        lambda: StaticSearchStore((Document(page_content="text", metadata=metadata), 0.9)),
    )

    with pytest.raises(VectorStoreUnavailable, match="normalize search results"):
        store.search("query", scopes=[current_scope()], limit=1)


class FailingClient:
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def collection_exists(self, *, collection_name: str) -> bool:
        del collection_name
        if self.operation == "collection_exists":
            raise RuntimeError("exists failed")
        return self.operation not in {"embedding", "create"}

    def create_collection(self, **_kwargs):
        if self.operation == "create":
            raise RuntimeError("create failed")

    def delete(self, **_kwargs):
        if self.operation.startswith("delete_"):
            raise RuntimeError("delete failed")


class FailingEmbeddings(StableTestEmbeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        del texts
        raise RuntimeError("embedding failed")


class FailingLangChainStore:
    def __init__(self, operation: str) -> None:
        self.operation = operation

    def add_documents(self, **_kwargs):
        if self.operation == "upsert":
            raise RuntimeError("upsert failed")

    def similarity_search_with_score(self, *_args, **_kwargs):
        if self.operation == "search":
            raise RuntimeError("search failed")
        return []


@pytest.mark.parametrize(
    "operation",
    [
        "collection_exists",
        "embedding",
        "create",
        "upsert",
        "search",
        "delete_generation",
        "delete_generations_before",
        "delete_document",
    ],
)
def test_every_external_vector_failure_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    client = FailingClient(operation)
    embeddings = (
        FailingEmbeddings()
        if operation == "embedding"
        else StableTestEmbeddings()
    )
    store = QdrantRetriever(
        settings=vector_settings(),
        client=client,  # type: ignore[arg-type]
        embeddings=embeddings,
    )
    monkeypatch.setattr(store, "_store", lambda: FailingLangChainStore(operation))

    with pytest.raises(VectorStoreUnavailable):
        if operation in {"embedding", "create", "upsert"}:
            store.upsert_generation(
                chunks_for("doc-1", generation=2, text="source")
            )
        elif operation in {"collection_exists", "search"}:
            store.search("query", scopes=[current_scope()], limit=1)
        elif operation == "delete_generation":
            store.delete_generation(current_scope())
        elif operation == "delete_generations_before":
            store.delete_generations_before(current_scope())
        else:
            store.delete_document(
                DocumentVectorScope(
                    collection_id="collection-1",
                    owner_user_id=7,
                    document_id="doc-1",
                )
            )


def test_qdrant_client_construction_failure_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_client(**_kwargs):
        raise RuntimeError("client failed")

    monkeypatch.setattr(
        "app.services.knowledge_retrievers.qdrant.QdrantClient",
        fail_client,
    )

    with pytest.raises(VectorStoreUnavailable, match="client construction"):
        build_qdrant_client(vector_settings())  # type: ignore[arg-type]


def test_embedding_construction_failure_is_mapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_embeddings(_settings):
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(
        "app.services.knowledge_retrievers.embedding.EmbeddingService",
        fail_embeddings,
    )

    with pytest.raises(VectorStoreUnavailable, match="embedding construction"):
        build_knowledge_embeddings(vector_settings())  # type: ignore[arg-type]


def test_store_construction_defers_embedding_configuration_until_use(
    monkeypatch: pytest.MonkeyPatch,
    qdrant_client: QdrantClient,
) -> None:
    calls: list[object] = []

    def fail_embeddings(_settings):
        calls.append(_settings)
        raise RuntimeError("embedding provider is not configured")

    monkeypatch.setattr(
        "app.services.knowledge_retrievers.qdrant.build_knowledge_embeddings",
        fail_embeddings,
    )

    store = QdrantRetriever(
        settings=vector_settings(),
        client=qdrant_client,
    )
    assert calls == []

    with pytest.raises(RuntimeError, match="embedding provider is not configured"):
        _ = store.embeddings
    assert len(calls) == 1


def test_vector_operation_maps_deferred_embedding_failure(
    monkeypatch: pytest.MonkeyPatch,
    qdrant_client: QdrantClient,
) -> None:
    def fail_embedding_service(_settings):
        raise RuntimeError("embedding provider is not configured")

    monkeypatch.setattr(
        "app.services.knowledge_retrievers.embedding.EmbeddingService",
        fail_embedding_service,
    )
    store = QdrantRetriever(
        settings=vector_settings(),
        client=qdrant_client,
    )

    with pytest.raises(VectorStoreUnavailable, match="embedding construction"):
        store.upsert_generation(chunks_for("doc-1", generation=1, text="source"))
