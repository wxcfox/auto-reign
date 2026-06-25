from typing import Any

import pytest
from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from app.repositories.vector_store import VectorStoreUnavailable, stable_vector_id
from app.services.embedding_service import DeterministicEmbeddings
from app.services.workspace_vector_store import WorkspaceVectorHit, WorkspaceVectorStore


class FakeLangChainQdrant:
    instances: list["FakeLangChainQdrant"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.added: list[tuple[list[Document], list[str]]] = []
        self.searches: list[dict[str, Any]] = []
        self.fail_add = kwargs.get("collection_name") == "fail_add"
        self.fail_search = kwargs.get("collection_name") == "fail_search"
        FakeLangChainQdrant.instances.append(self)

    def add_documents(self, documents: list[Document], ids: list[str]) -> None:
        if self.fail_add:
            raise RuntimeError("add failed")
        self.added.append((documents, ids))

    def similarity_search_with_score(
        self,
        query: str,
        k: int,
        filter: Filter | None = None,
    ) -> list[tuple[Document, float]]:
        if self.fail_search:
            raise RuntimeError("search failed")
        self.searches.append({"query": query, "k": k, "filter": filter})
        return [
            (
                Document(
                    page_content="Redis cache stampede",
                    metadata={"artifact_id": "a1", "artifact_kind": "knowledge"},
                ),
                0.88,
            )
        ]


class FakeQdrantClient:
    def __init__(
        self,
        *,
        collection_names: list[str] | None = None,
        count: int = 1,
        fail_on: str | None = None,
    ) -> None:
        self.collection_names = collection_names if collection_names is not None else ["workspace"]
        self.count_value = count
        self.fail_on = fail_on
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def collection_exists(self, **kwargs: Any) -> bool:
        self._record("collection_exists", kwargs)
        return kwargs["collection_name"] in self.collection_names

    def count(self, **kwargs: Any):
        self._record("count", kwargs)

        class Count:
            count = self.count_value

        return Count()

    def create_collection(self, **kwargs: Any) -> bool:
        self._record("create_collection", kwargs)
        self.collection_names.append(kwargs["collection_name"])
        return True

    def delete(self, **kwargs: Any) -> None:
        self._record("delete", kwargs)

    def delete_collection(self, **kwargs: Any) -> None:
        self._record("delete_collection", kwargs)
        self.collection_names = [
            name for name in self.collection_names if name != kwargs["collection_name"]
        ]

    def get_collections(self):
        self._record("get_collections", {})

        class Collection:
            def __init__(self, name: str) -> None:
                self.name = name

        class Response:
            collections = [Collection(name) for name in self.collection_names]

        return Response()

    def _record(self, operation: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((operation, kwargs))
        if self.fail_on == operation:
            raise RuntimeError(f"{operation} failed")


@pytest.fixture(autouse=True)
def reset_fake_langchain_qdrant() -> None:
    FakeLangChainQdrant.instances = []


def make_store(client: FakeQdrantClient | None = None) -> WorkspaceVectorStore:
    return WorkspaceVectorStore(
        client=client or FakeQdrantClient(),
        embeddings=DeterministicEmbeddings(),
    )


def test_upsert_documents_uses_langchain_qdrant_with_stable_chunk_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    store = make_store()
    documents = [
        Document(page_content="body", metadata={"artifact_id": "a1", "chunk_index": 0}),
        Document(page_content="fallback", metadata={"source_id": "s1", "chunk_index": 2}),
    ]

    store.upsert_documents("workspace", documents)

    instance = FakeLangChainQdrant.instances[-1]
    assert instance.kwargs["collection_name"] == "workspace"
    assert instance.added == [
        (
            documents,
            [
                stable_vector_id("artifact", "a1", 0),
                stable_vector_id("artifact", "s1", 2),
            ],
        )
    ]


def test_upsert_documents_is_a_no_op_for_empty_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    client = FakeQdrantClient()
    store = make_store(client)

    store.upsert_documents("workspace", [])

    assert FakeLangChainQdrant.instances == []
    assert client.calls == []


def test_upsert_documents_maps_langchain_failures_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    store = make_store()
    documents = [Document(page_content="body", metadata={"artifact_id": "a1", "chunk_index": 0})]

    with pytest.raises(VectorStoreUnavailable):
        store.upsert_documents("fail_add", documents)


def test_search_returns_empty_when_collection_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    client = FakeQdrantClient(collection_names=[])
    store = make_store(client)

    assert store.search("workspace", "Redis", limit=4) == []
    assert FakeLangChainQdrant.instances == []
    assert client.calls == [("collection_exists", {"collection_name": "workspace"})]


def test_search_returns_empty_when_collection_has_no_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    client = FakeQdrantClient(count=0)
    store = make_store(client)

    assert store.search("workspace", "Redis", limit=4) == []
    assert FakeLangChainQdrant.instances == []
    assert client.calls == [
        ("collection_exists", {"collection_name": "workspace"}),
        ("count", {"collection_name": "workspace", "exact": False}),
    ]


def test_search_maps_langchain_results_and_metadata_filter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    metadata_filter = Filter(
        must=[
            FieldCondition(
                key="metadata.artifact_kind",
                match=MatchValue(value="knowledge"),
            )
        ]
    )
    store = make_store()

    hits = store.search("workspace", "Redis", limit=4, metadata_filter=metadata_filter)

    assert hits == [
        WorkspaceVectorHit(
            content="Redis cache stampede",
            score=0.88,
            metadata={"artifact_id": "a1", "artifact_kind": "knowledge"},
        )
    ]
    instance = FakeLangChainQdrant.instances[-1]
    assert instance.searches == [{"query": "Redis", "k": 4, "filter": metadata_filter}]


def test_search_maps_langchain_failures_to_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.workspace_vector_store.QdrantVectorStore",
        FakeLangChainQdrant,
    )
    store = make_store(FakeQdrantClient(collection_names=["fail_search"]))

    with pytest.raises(VectorStoreUnavailable):
        store.search("fail_search", "Redis", limit=4)


def test_delete_artifact_chunks_uses_qdrant_metadata_filter() -> None:
    client = FakeQdrantClient()
    store = make_store(client)

    store.delete_artifact_chunks("workspace", "a1")

    assert [name for name, _ in client.calls] == ["collection_exists", "delete"]
    delete_call = client.calls[1][1]
    assert delete_call["collection_name"] == "workspace"
    assert delete_call["wait"] is True
    selector = delete_call["points_selector"]
    assert selector.filter.must is not None
    condition = selector.filter.must[0]
    assert condition.key == "metadata.artifact_id"
    assert condition.match.value == "a1"


def test_delete_artifact_chunks_is_a_no_op_when_collection_is_absent() -> None:
    client = FakeQdrantClient(collection_names=[])
    store = make_store(client)

    store.delete_artifact_chunks("workspace", "a1")

    assert client.calls == [("collection_exists", {"collection_name": "workspace"})]


def test_admin_helpers_use_qdrant_client() -> None:
    client = FakeQdrantClient(collection_names=["workspace", "workspace_2"])
    store = make_store(client)

    assert store.has_searchable_content("workspace") is True
    assert store.list_collections() == ["workspace", "workspace_2"]
    store.delete_collection("workspace")

    assert [name for name, _ in client.calls] == [
        "collection_exists",
        "count",
        "get_collections",
        "collection_exists",
        "delete_collection",
    ]
    assert client.collection_names == ["workspace_2"]


def test_qdrant_client_failures_map_to_unavailable() -> None:
    store = make_store(FakeQdrantClient(fail_on="collection_exists"))

    with pytest.raises(VectorStoreUnavailable):
        store.has_searchable_content("workspace")


def test_real_memory_qdrant_upserts_searches_and_deletes_artifact_chunks() -> None:
    client = QdrantClient(location=":memory:")
    store = WorkspaceVectorStore(client=client, embeddings=DeterministicEmbeddings())
    try:
        store.upsert_documents(
            "workspace",
            [
                Document(
                    page_content="Redis cache stampede mutex lock",
                    metadata={"artifact_id": "a1", "artifact_kind": "knowledge", "chunk_index": 0},
                ),
                Document(
                    page_content="MySQL covering index",
                    metadata={"artifact_id": "a2", "artifact_kind": "knowledge", "chunk_index": 0},
                ),
            ],
        )

        assert client.count(collection_name="workspace", exact=True).count == 2
        hits = store.search("workspace", "Redis mutex", limit=2)
        assert [hit.metadata["artifact_id"] for hit in hits]
        filtered_hits = store.search(
            "workspace",
            "Redis mutex",
            limit=2,
            metadata_filter=Filter(
                must=[
                    FieldCondition(
                        key="metadata.artifact_id",
                        match=MatchValue(value="a1"),
                    )
                ]
            ),
        )
        assert {hit.metadata["artifact_id"] for hit in filtered_hits} == {"a1"}
        assert all("_id" in hit.metadata for hit in filtered_hits)
        assert all(hit.metadata["_collection_name"] == "workspace" for hit in filtered_hits)

        store.delete_artifact_chunks("workspace", "a1")

        points, _ = client.scroll(collection_name="workspace", with_payload=True)
        payloads = [point.payload for point in points]
        assert len(payloads) == 1
        assert payloads[0]["metadata"]["artifact_id"] == "a2"
    finally:
        client.close()
