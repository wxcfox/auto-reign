from __future__ import annotations

import math
import os
from types import SimpleNamespace
from uuid import uuid4

from elasticsearch import Elasticsearch
from langchain_core.embeddings import Embeddings
import pytest

from app.services.knowledge_chunk_service import KnowledgeChunk
from app.services.knowledge_retrievers import DocumentGeneration, DocumentIndexScope
from app.services.knowledge_retrievers.elasticsearch import ElasticsearchRetriever


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ELASTICSEARCH_INTEGRATION") != "1",
    reason="requires RUN_ELASTICSEARCH_INTEGRATION=1 and Elasticsearch 8.19.3",
)


class IntegrationEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        lowered = text.casefold()
        if "lexical" in lowered:
            return [0.0, 1.0, 0.0]
        if "blended" in lowered:
            value = 1.0 / math.sqrt(2.0)
            return [value, value, 0.0]
        return [1.0, 0.0, 0.0]


def _settings(*, url: str, index_name: str):
    return SimpleNamespace(
        elasticsearch_url=url,
        elasticsearch_index=index_name,
        elasticsearch_username=None,
        elasticsearch_password=None,
        elasticsearch_api_key=None,
        elasticsearch_request_timeout_seconds=10.0,
    )


def _scope(
    *,
    document_id: str,
    generation: int,
    content_hash: str,
    owner_user_id: int = 7,
) -> DocumentGeneration:
    return DocumentGeneration(
        collection_id="collection-integration",
        owner_user_id=owner_user_id,
        document_id=document_id,
        index_generation=generation,
        content_hash=content_hash,
    )


def _chunk(
    content: str,
    *,
    document_id: str,
    generation: int,
    content_hash: str,
    chunk_index: int,
    owner_user_id: int = 7,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        content=content,
        metadata={
            "owner_user_id": owner_user_id,
            "collection_id": "collection-integration",
            "document_id": document_id,
            "index_generation": generation,
            "content_hash": content_hash,
            "filename": f"{document_id}.txt",
            "chunk_index": chunk_index,
            "source_start": chunk_index * 100,
            "source_end": chunk_index * 100 + len(content),
        },
    )


@pytest.fixture
def live_retriever():
    url = os.environ.get("ELASTICSEARCH_URL", "http://127.0.0.1:19200")
    index_name = f"auto_reign_knowledge_integration_{uuid4().hex}"
    client = Elasticsearch(url, request_timeout=10)
    info = client.info()
    assert info["version"]["number"] == "8.19.3"
    retriever = ElasticsearchRetriever(
        settings=_settings(url=url, index_name=index_name),
        client=client,
        embeddings=IntegrationEmbeddings(),
    )
    try:
        yield retriever, client, index_name
    finally:
        client.indices.delete(index=index_name, ignore_unavailable=True)
        client.close()


def _seed_retrieval_scenarios(retriever: ElasticsearchRetriever) -> None:
    retriever.upsert_generation(
        [
            _chunk(
                "signal semantic passage",
                document_id="doc-current",
                generation=2,
                content_hash="hash-current",
                chunk_index=0,
            ),
            _chunk(
                "signal signal lexical passage",
                document_id="doc-current",
                generation=2,
                content_hash="hash-current",
                chunk_index=1,
            ),
            _chunk(
                "signal blended passage",
                document_id="doc-current",
                generation=2,
                content_hash="hash-current",
                chunk_index=2,
            ),
        ]
    )
    retriever.upsert_generation(
        [
            _chunk(
                "signal obsolete passage",
                document_id="doc-current",
                generation=1,
                content_hash="hash-old",
                chunk_index=0,
            )
        ]
    )
    retriever.upsert_generation(
        [
            _chunk(
                "signal foreign passage",
                document_id="doc-foreign",
                generation=2,
                content_hash="hash-foreign",
                chunk_index=0,
                owner_user_id=8,
            )
        ]
    )


def _retrieve(
    retriever: ElasticsearchRetriever,
    *,
    scope: DocumentGeneration,
    mode: str,
):
    return retriever.retrieve(
        "signal",
        scopes=[scope],
        mode=mode,  # type: ignore[arg-type]
        limit=3,
        vector_weight=0.7,
        keyword_weight=0.3,
    )


def _chunk_key(hit) -> tuple[object, ...]:
    metadata = hit.metadata
    return (
        metadata["document_id"],
        metadata["index_generation"],
        metadata["content_hash"],
        metadata["chunk_index"],
    )


def test_real_elasticsearch_mapping_upsert_retrieval_and_filters(live_retriever) -> None:
    retriever, client, index_name = live_retriever
    _seed_retrieval_scenarios(retriever)
    current = _scope(
        document_id="doc-current",
        generation=2,
        content_hash="hash-current",
    )

    mapping = client.indices.get_mapping(index=index_name)[index_name]["mappings"]
    properties = mapping["properties"]
    assert properties["content"]["type"] == "text"
    assert properties["embedding"]["type"] == "dense_vector"
    assert properties["embedding"]["dims"] == 3
    assert properties["embedding"]["index"] is True
    assert properties["embedding"]["similarity"] == "cosine"
    metadata_properties = properties["metadata"]["properties"]
    assert set(metadata_properties) == {
        "owner_user_id",
        "collection_id",
        "document_id",
        "index_generation",
        "content_hash",
        "filename",
        "chunk_index",
        "source_start",
        "source_end",
    }
    assert client.count(index=index_name)["count"] == 5
    indexed = client.get(
        index=index_name,
        id=next(
            hit["_id"]
            for hit in client.search(
                index=index_name,
                query={"term": {"metadata.chunk_index": 0}},
                size=10,
            )["hits"]["hits"]
            if hit["_source"]["metadata"]["document_id"] == "doc-current"
            and hit["_source"]["metadata"]["index_generation"] == 2
        ),
    )["_source"]
    assert indexed["content"] == "signal semantic passage"
    assert indexed["embedding"] == pytest.approx([1.0, 0.0, 0.0])

    vector_hits = _retrieve(retriever, scope=current, mode="vector")
    keyword_hits = _retrieve(retriever, scope=current, mode="keyword")
    hybrid_hits = _retrieve(retriever, scope=current, mode="hybrid")

    for hits in (vector_hits, keyword_hits, hybrid_hits):
        assert len(hits) == 3
        assert all(hit.metadata["owner_user_id"] == 7 for hit in hits)
        assert all(hit.metadata["index_generation"] == 2 for hit in hits)
        assert all(hit.metadata["content_hash"] == "hash-current" for hit in hits)
        assert all(0.0 <= hit.score <= 1.0 for hit in hits)
    assert all(hit.vector_score == hit.score for hit in vector_hits)
    assert all(hit.keyword_score == hit.score for hit in keyword_hits)

    vector_by_chunk = {_chunk_key(hit): hit.score for hit in vector_hits}
    keyword_by_chunk = {_chunk_key(hit): hit.score for hit in keyword_hits}
    assert {_chunk_key(hit) for hit in hybrid_hits} == set(vector_by_chunk) | set(
        keyword_by_chunk
    )
    for hit in hybrid_hits:
        key = _chunk_key(hit)
        expected = 0.7 * vector_by_chunk.get(key, 0.0) + 0.3 * keyword_by_chunk.get(
            key, 0.0
        )
        assert hit.vector_score == pytest.approx(vector_by_chunk.get(key, 0.0))
        assert hit.keyword_score == pytest.approx(keyword_by_chunk.get(key, 0.0))
        assert hit.fused_score == pytest.approx(expected)
        assert hit.score == pytest.approx(expected)
    assert [hit.score for hit in hybrid_hits] == sorted(
        (hit.score for hit in hybrid_hits), reverse=True
    )


def test_real_elasticsearch_delete_generation_and_document(live_retriever) -> None:
    retriever, client, index_name = live_retriever
    _seed_retrieval_scenarios(retriever)
    old = _scope(
        document_id="doc-current",
        generation=1,
        content_hash="hash-old",
    )

    retriever.delete_generation(old)

    assert client.count(
        index=index_name,
        query={
            "bool": {
                "filter": [
                    {"term": {"metadata.document_id": "doc-current"}},
                    {"term": {"metadata.index_generation": 1}},
                ]
            }
        },
    )["count"] == 0
    assert client.count(index=index_name)["count"] == 4

    retriever.delete_document(
        DocumentIndexScope(
            collection_id="collection-integration",
            owner_user_id=7,
            document_id="doc-current",
        )
    )

    assert client.count(
        index=index_name,
        query={"term": {"metadata.document_id": "doc-current"}},
    )["count"] == 0
    remaining = client.search(index=index_name, query={"match_all": {}})["hits"]["hits"]
    assert len(remaining) == 1
    assert remaining[0]["_source"]["metadata"]["owner_user_id"] == 8
