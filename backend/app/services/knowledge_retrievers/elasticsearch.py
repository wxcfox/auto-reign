from __future__ import annotations

from collections.abc import Callable
import math
from typing import TypeVar

from elasticsearch import Elasticsearch, helpers
from elasticsearch.exceptions import NotFoundError
from langchain_core.embeddings import Embeddings

from app.core.config import Settings
from app.repositories.vector_store import VectorStoreUnavailable, stable_vector_id
from app.services.embedding_service import EmbeddingProviderError
from app.services.knowledge_chunk_service import KnowledgeChunk
from app.services.knowledge_retrievers.base import (
    DocumentGeneration,
    DocumentIndexScope,
    KnowledgeRetrieverHit,
    RetrievalMode,
)
from app.services.knowledge_retrievers.embedding import build_knowledge_embeddings


_Result = TypeVar("_Result")


class ElasticsearchRetriever:
    retriever_type = "elasticsearch"
    supported_retrieval_methods = frozenset({"vector", "keyword", "hybrid"})

    def __init__(
        self,
        *,
        settings: Settings,
        client: Elasticsearch | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings
        self.index_name = settings.elasticsearch_index
        if client is None:
            authentication: dict[str, object] = {}
            if settings.elasticsearch_api_key:
                authentication["api_key"] = settings.elasticsearch_api_key
            elif settings.elasticsearch_username and settings.elasticsearch_password:
                authentication["basic_auth"] = (
                    settings.elasticsearch_username,
                    settings.elasticsearch_password,
                )
            client = Elasticsearch(
                settings.elasticsearch_url,
                request_timeout=settings.elasticsearch_request_timeout_seconds,
                **authentication,
            )
        self.client = client
        self._embeddings = embeddings

    @property
    def embeddings(self) -> Embeddings:
        if self._embeddings is None:
            self._embeddings = build_knowledge_embeddings(self.settings)
        return self._embeddings

    @staticmethod
    def _external(operation: str, callback: Callable[[], _Result]) -> _Result:
        try:
            return callback()
        except EmbeddingProviderError:
            raise
        except VectorStoreUnavailable:
            raise
        except Exception as error:
            raise VectorStoreUnavailable(
                f"Knowledge Elasticsearch {operation} failed"
            ) from error

    @staticmethod
    def _validate_generation(scope: DocumentGeneration) -> None:
        if (
            not scope.collection_id
            or type(scope.owner_user_id) is not int
            or scope.owner_user_id < 0
            or not scope.document_id
            or type(scope.index_generation) is not int
            or scope.index_generation < 1
            or not scope.content_hash
        ):
            raise ValueError("knowledge generation scope is invalid")

    @classmethod
    def _generation_filter(cls, scope: DocumentGeneration) -> dict[str, object]:
        cls._validate_generation(scope)
        return {
            "bool": {
                "filter": [
                    {"term": {"metadata.collection_id": scope.collection_id}},
                    {"term": {"metadata.owner_user_id": scope.owner_user_id}},
                    {"term": {"metadata.document_id": scope.document_id}},
                    {"term": {"metadata.index_generation": scope.index_generation}},
                    {"term": {"metadata.content_hash": scope.content_hash}},
                ]
            }
        }

    @classmethod
    def _scope_query(cls, scopes: list[DocumentGeneration]) -> dict[str, object]:
        if not scopes:
            raise ValueError("knowledge retriever scopes must not be empty")
        return {
            "bool": {
                "should": [cls._generation_filter(scope) for scope in scopes],
                "minimum_should_match": 1,
            }
        }

    def _ensure_index(self, dimension: int) -> None:
        if self._external(
            "index existence check",
            lambda: bool(self.client.indices.exists(index=self.index_name)),
        ):
            return
        mappings = {
            "dynamic": "strict",
            "properties": {
                "content": {"type": "text"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": dimension,
                    "index": True,
                    "similarity": "cosine",
                },
                "metadata": {
                    "type": "object",
                    "dynamic": "strict",
                    "properties": {
                        "owner_user_id": {"type": "long"},
                        "collection_id": {"type": "keyword"},
                        "document_id": {"type": "keyword"},
                        "index_generation": {"type": "long"},
                        "content_hash": {"type": "keyword"},
                        "filename": {"type": "keyword"},
                        "chunk_index": {"type": "long"},
                        "source_start": {"type": "long"},
                        "source_end": {"type": "long"},
                    },
                },
            },
        }
        self._external(
            "index creation",
            lambda: self.client.indices.create(
                index=self.index_name,
                mappings=mappings,
            ),
        )

    def upsert_generation(self, chunks: list[KnowledgeChunk]) -> None:
        if not chunks:
            return
        required = {
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
        for chunk in chunks:
            if not required.issubset(chunk.metadata) or not chunk.content.strip():
                raise ValueError("knowledge chunk payload is incomplete")
        vectors = self._external(
            "embedding",
            lambda: self.embeddings.embed_documents([chunk.content for chunk in chunks]),
        )
        if len(vectors) != len(chunks) or not vectors or not vectors[0]:
            raise VectorStoreUnavailable("Knowledge embedding returned invalid vectors")
        dimension = len(vectors[0])
        if any(len(vector) != dimension for vector in vectors):
            raise VectorStoreUnavailable("Knowledge embedding dimensions are inconsistent")
        self._ensure_index(dimension)
        actions: list[dict[str, object]] = []
        seen_ids: set[str] = set()
        for chunk, vector in zip(chunks, vectors, strict=True):
            metadata = dict(chunk.metadata)
            scope = DocumentGeneration(
                collection_id=metadata["collection_id"],  # type: ignore[arg-type]
                owner_user_id=metadata["owner_user_id"],  # type: ignore[arg-type]
                document_id=metadata["document_id"],  # type: ignore[arg-type]
                index_generation=metadata["index_generation"],  # type: ignore[arg-type]
                content_hash=metadata["content_hash"],  # type: ignore[arg-type]
            )
            self._validate_generation(scope)
            point_id = stable_vector_id(
                "knowledge",
                scope.document_id,
                scope.index_generation,
                metadata["chunk_index"],
            )
            if point_id in seen_ids:
                raise ValueError("knowledge chunk ids must be unique")
            seen_ids.add(point_id)
            actions.append(
                {
                    "_op_type": "index",
                    "_index": self.index_name,
                    "_id": point_id,
                    "_source": {
                        "content": chunk.content,
                        "embedding": vector,
                        "metadata": metadata,
                    },
                }
            )
        self._external(
            "upsert",
            lambda: helpers.bulk(self.client, actions, refresh="wait_for"),
        )

    def retrieve(
        self,
        query: str,
        *,
        scopes: list[DocumentGeneration],
        mode: RetrievalMode,
        limit: int,
        vector_weight: float,
        keyword_weight: float,
    ) -> list[KnowledgeRetrieverHit]:
        if mode not in self.supported_retrieval_methods:
            raise ValueError(f"Elasticsearch does not support '{mode}' retrieval mode")
        if not query.strip() or type(limit) is not int or limit < 1:
            raise ValueError("knowledge retrieval query and limit are required")
        if not self._external(
            "index existence check",
            lambda: bool(self.client.indices.exists(index=self.index_name)),
        ):
            raise VectorStoreUnavailable("Knowledge Elasticsearch index is unavailable")
        scope_query = self._scope_query(scopes)
        if mode == "vector":
            return self._vector_search(query, scope_query=scope_query, limit=limit)
        if mode == "keyword":
            return self._keyword_search(query, scope_query=scope_query, limit=limit)
        return self._hybrid_search(
            query,
            scope_query=scope_query,
            limit=limit,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
        )

    def _vector_search(
        self,
        query: str,
        *,
        scope_query: dict[str, object],
        limit: int,
    ) -> list[KnowledgeRetrieverHit]:
        vector = self._external("query embedding", lambda: self.embeddings.embed_query(query))
        response = self._external(
            "vector search",
            lambda: self.client.search(
                index=self.index_name,
                size=limit,
                source_excludes=["embedding"],
                query={
                    "script_score": {
                        "query": scope_query,
                        "script": {
                            "source": "(cosineSimilarity(params.vector, 'embedding') + 1.0) / 2.0",
                            "params": {"vector": vector},
                        },
                    }
                },
            ),
        )
        return self._hits(response, mode="vector")

    def _keyword_search(
        self,
        query: str,
        *,
        scope_query: dict[str, object],
        limit: int,
    ) -> list[KnowledgeRetrieverHit]:
        response = self._external(
            "keyword search",
            lambda: self.client.search(
                index=self.index_name,
                size=limit,
                source_excludes=["embedding"],
                query={
                    "bool": {
                        "must": [{"match": {"content": {"query": query}}}],
                        "filter": [scope_query],
                    }
                },
            ),
        )
        raw_hits = self._hits(response, mode="keyword", normalize_keyword=False)
        max_score = max((hit.score for hit in raw_hits), default=1.0)
        divisor = max_score if max_score > 1.0 else 1.0
        return [
            KnowledgeRetrieverHit(
                content=hit.content,
                score=hit.score / divisor,
                metadata=hit.metadata,
                retrieval_mode="keyword",
                keyword_score=hit.score / divisor,
            )
            for hit in raw_hits
        ]

    def _hybrid_search(
        self,
        query: str,
        *,
        scope_query: dict[str, object],
        limit: int,
        vector_weight: float,
        keyword_weight: float,
    ) -> list[KnowledgeRetrieverHit]:
        total_weight = vector_weight + keyword_weight
        if total_weight <= 0:
            raise ValueError("hybrid retrieval weights must have a positive sum")
        resolved_vector_weight = vector_weight / total_weight
        resolved_keyword_weight = keyword_weight / total_weight
        vector_hits = self._vector_search(query, scope_query=scope_query, limit=limit)
        keyword_hits = self._keyword_search(query, scope_query=scope_query, limit=limit)
        merged: dict[tuple[object, ...], dict[str, object]] = {}
        for hit in vector_hits:
            merged[self._chunk_key(hit.metadata)] = {
                "content": hit.content,
                "metadata": hit.metadata,
                "vector_score": hit.score,
                "keyword_score": 0.0,
            }
        for hit in keyword_hits:
            record = merged.setdefault(
                self._chunk_key(hit.metadata),
                {
                    "content": hit.content,
                    "metadata": hit.metadata,
                    "vector_score": 0.0,
                    "keyword_score": 0.0,
                },
            )
            if record["content"] != hit.content or record["metadata"] != hit.metadata:
                raise VectorStoreUnavailable("Hybrid retrieval chunk identity conflicted")
            record["keyword_score"] = hit.score
        fused: list[KnowledgeRetrieverHit] = []
        for record in merged.values():
            vector_score = float(record["vector_score"])
            keyword_score = float(record["keyword_score"])
            fused_score = (
                resolved_vector_weight * vector_score
                + resolved_keyword_weight * keyword_score
            )
            fused.append(
                KnowledgeRetrieverHit(
                    content=record["content"],  # type: ignore[arg-type]
                    score=fused_score,
                    metadata=record["metadata"],  # type: ignore[arg-type]
                    retrieval_mode="hybrid",
                    vector_score=vector_score,
                    keyword_score=keyword_score,
                    fused_score=fused_score,
                )
            )
        fused.sort(key=lambda hit: (-hit.score, self._chunk_key(hit.metadata)))
        return fused[:limit]

    @classmethod
    def _hits(
        cls,
        response,
        *,
        mode: RetrievalMode,
        normalize_keyword: bool = True,
    ) -> list[KnowledgeRetrieverHit]:
        del normalize_keyword
        normalized: list[KnowledgeRetrieverHit] = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            content = source.get("content")
            metadata = source.get("metadata")
            score = hit.get("_score")
            if (
                not isinstance(content, str)
                or not content.strip()
                or not isinstance(metadata, dict)
                or isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(float(score))
            ):
                raise VectorStoreUnavailable("Elasticsearch returned an invalid hit")
            score_value = float(score)
            if (mode == "vector" and not 0.0 <= score_value <= 1.0) or (
                mode == "keyword" and score_value < 0.0
            ):
                raise VectorStoreUnavailable("Elasticsearch returned an invalid score")
            normalized.append(
                KnowledgeRetrieverHit(
                    content=content,
                    score=score_value,
                    metadata=dict(metadata),
                    retrieval_mode=mode,
                    vector_score=score_value if mode == "vector" else None,
                    keyword_score=score_value if mode == "keyword" else None,
                )
            )
        return normalized

    @staticmethod
    def _chunk_key(metadata: dict[str, object]) -> tuple[object, ...]:
        return (
            metadata.get("collection_id"),
            metadata.get("owner_user_id"),
            metadata.get("document_id"),
            metadata.get("index_generation"),
            metadata.get("content_hash"),
            metadata.get("chunk_index"),
            metadata.get("source_start"),
            metadata.get("source_end"),
        )

    def _delete_by_query(self, query: dict[str, object]) -> None:
        try:
            self._external(
                "delete by query",
                lambda: self.client.delete_by_query(
                    index=self.index_name,
                    query=query,
                    refresh=True,
                    conflicts="proceed",
                ),
            )
        except VectorStoreUnavailable as error:
            if isinstance(error.__cause__, NotFoundError):
                return
            raise

    def delete_generation(self, scope: DocumentGeneration) -> None:
        self._delete_by_query(self._generation_filter(scope))

    def delete_generations_before(self, current: DocumentGeneration) -> None:
        self._validate_generation(current)
        if current.index_generation <= 1:
            return
        self._delete_by_query(
            {
                "bool": {
                    "filter": [
                        {"term": {"metadata.collection_id": current.collection_id}},
                        {"term": {"metadata.owner_user_id": current.owner_user_id}},
                        {"term": {"metadata.document_id": current.document_id}},
                        {"range": {"metadata.index_generation": {"lt": current.index_generation}}},
                    ]
                }
            }
        )

    def delete_document(self, scope: DocumentIndexScope) -> None:
        if not scope.collection_id or scope.owner_user_id < 0 or not scope.document_id:
            raise ValueError("knowledge document scope is invalid")
        self._delete_by_query(
            {
                "bool": {
                    "filter": [
                        {"term": {"metadata.collection_id": scope.collection_id}},
                        {"term": {"metadata.owner_user_id": scope.owner_user_id}},
                        {"term": {"metadata.document_id": scope.document_id}},
                    ]
                }
            }
        )

    def purge_collection(self, *, collection_id: str, owner_user_id: int) -> None:
        if not collection_id or type(owner_user_id) is not int or owner_user_id < 0:
            raise ValueError("knowledge collection scope is invalid")
        self._delete_by_query(
            {
                "bool": {
                    "filter": [
                        {"term": {"metadata.collection_id": collection_id}},
                        {"term": {"metadata.owner_user_id": owner_user_id}},
                    ]
                }
            }
        )

    def test_connection(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False
