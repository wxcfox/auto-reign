from __future__ import annotations

from collections.abc import Callable
import math
from typing import TypeVar

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    Range,
    VectorParams,
)

from app.core.config import Settings, get_settings
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


_COSINE_SCORE_EPSILON = 1e-6


def document_scope_conditions(
    scope: DocumentIndexScope | DocumentGeneration,
) -> list[FieldCondition]:
    if (
        not isinstance(scope.collection_id, str)
        or not scope.collection_id
        or type(scope.owner_user_id) is not int
        or scope.owner_user_id < 0
        or not isinstance(scope.document_id, str)
        or not scope.document_id
    ):
        raise ValueError("knowledge document vector scope is invalid")
    return [
        FieldCondition(
            key="metadata.collection_id",
            match=MatchValue(value=scope.collection_id),
        ),
        FieldCondition(
            key="metadata.owner_user_id",
            match=MatchValue(value=scope.owner_user_id),
        ),
        FieldCondition(
            key="metadata.document_id",
            match=MatchValue(value=scope.document_id),
        ),
    ]


def exact_generation_conditions(
    scope: DocumentGeneration,
) -> list[FieldCondition]:
    if (
        type(scope.index_generation) is not int
        or scope.index_generation < 1
        or not isinstance(scope.content_hash, str)
        or not scope.content_hash
    ):
        raise ValueError("knowledge generation vector scope is invalid")
    return [
        *document_scope_conditions(scope),
        FieldCondition(
            key="metadata.index_generation",
            match=MatchValue(value=scope.index_generation),
        ),
        FieldCondition(
            key="metadata.content_hash",
            match=MatchValue(value=scope.content_hash),
        ),
    ]


def build_generation_filter(scopes: list[DocumentGeneration]) -> Filter:
    if not scopes:
        raise ValueError("knowledge vector scopes must not be empty")
    return Filter(
        should=[Filter(must=exact_generation_conditions(scope)) for scope in scopes]
    )


def build_qdrant_client(settings: Settings) -> QdrantClient:
    try:
        if settings.qdrant_url == ":memory:":
            return QdrantClient(location=":memory:")
        return QdrantClient(
            url=settings.qdrant_url,
            api_key=getattr(settings, "qdrant_api_key", None),
        )
    except Exception as error:
        raise VectorStoreUnavailable(
            "Knowledge vector client construction failed"
        ) from error


_Result = TypeVar("_Result")


class QdrantRetriever:
    retriever_type = "qdrant"
    supported_retrieval_methods = frozenset({"vector"})

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: QdrantClient | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client if client is not None else build_qdrant_client(self.settings)
        self._embeddings = embeddings
        self.collection_name = self.settings.qdrant_collection

    @property
    def embeddings(self) -> Embeddings:
        """Build embeddings only when a vector operation actually needs them."""
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
                f"Knowledge vector {operation} failed"
            ) from error

    def _collection_exists(self) -> bool:
        return bool(
            self._external(
                "collection_exists",
                lambda: self.client.collection_exists(
                    collection_name=self.collection_name
                ),
            )
        )

    def _store(self) -> QdrantVectorStore:
        return QdrantVectorStore(
            client=self.client,
            collection_name=self.collection_name,
            embedding=self.embeddings,
        )

    def _ensure_collection(self, documents: list[Document]) -> None:
        if self._collection_exists():
            return
        probe = next(
            (document.page_content for document in documents if document.page_content),
            "probe",
        )
        vectors = self._external(
            "embedding dimension probe",
            lambda: self.embeddings.embed_documents([probe]),
        )
        if not vectors or not vectors[0]:
            raise VectorStoreUnavailable(
                "Knowledge embedding probe returned no vector"
            )
        dimension = len(vectors[0])
        self._external(
            "create collection",
            lambda: self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=dimension,
                    distance=Distance.COSINE,
                ),
            ),
        )

    def upsert_generation(self, chunks: list[KnowledgeChunk]) -> None:
        if not chunks:
            return
        required = {
            "collection_id",
            "owner_user_id",
            "document_id",
            "index_generation",
            "content_hash",
            "filename",
            "chunk_index",
            "source_start",
            "source_end",
        }
        documents: list[Document] = []
        ids: list[str] = []
        for chunk in chunks:
            if not required.issubset(chunk.metadata):
                raise ValueError("knowledge chunk metadata is incomplete")
            scope = DocumentGeneration(
                collection_id=chunk.metadata["collection_id"],  # type: ignore[arg-type]
                owner_user_id=chunk.metadata["owner_user_id"],  # type: ignore[arg-type]
                document_id=chunk.metadata["document_id"],  # type: ignore[arg-type]
                index_generation=chunk.metadata["index_generation"],  # type: ignore[arg-type]
                content_hash=chunk.metadata["content_hash"],  # type: ignore[arg-type]
            )
            exact_generation_conditions(scope)
            filename = chunk.metadata["filename"]
            chunk_index = chunk.metadata["chunk_index"]
            if (
                not isinstance(chunk.content, str)
                or not chunk.content.strip()
                or not isinstance(filename, str)
                or not filename
                or type(chunk_index) is not int
                or chunk_index < 0
            ):
                raise ValueError("knowledge chunk payload is invalid")
            documents.append(
                Document(page_content=chunk.content, metadata=dict(chunk.metadata))
            )
            ids.append(
                stable_vector_id(
                    "knowledge",
                    scope.document_id,
                    scope.index_generation,
                    chunk_index,
                )
            )
        if len(ids) != len(set(ids)):
            raise ValueError("knowledge chunk point ids must be unique")

        self._ensure_collection(documents)
        self._external(
            "upsert",
            lambda: self._store().add_documents(documents=documents, ids=ids),
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
        del vector_weight, keyword_weight
        if mode != "vector":
            raise ValueError(
                f"Qdrant does not support '{mode}' retrieval mode; "
                "supported modes: vector"
            )
        return self.search(query, scopes=scopes, limit=limit)

    def search(
        self,
        query: str,
        *,
        scopes: list[DocumentGeneration],
        limit: int,
    ) -> list[KnowledgeRetrieverHit]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("knowledge vector query must not be empty")
        if type(limit) is not int or limit < 1:
            raise ValueError("knowledge vector limit must be positive")
        metadata_filter = build_generation_filter(scopes)
        if not self._collection_exists():
            raise VectorStoreUnavailable(
                "Knowledge vector collection is unavailable"
            )
        results = self._external(
            "search",
            lambda: self._store().similarity_search_with_score(
                query,
                k=limit,
                filter=metadata_filter,
            ),
        )
        allowed = {
            (
                scope.collection_id,
                scope.owner_user_id,
                scope.document_id,
                scope.index_generation,
                scope.content_hash,
            )
            for scope in scopes
        }

        def normalize_results() -> list[KnowledgeRetrieverHit]:
            normalized: list[KnowledgeRetrieverHit] = []
            for document, score in results:
                if not isinstance(document.metadata, dict):
                    raise ValueError("invalid knowledge vector result")
                metadata = dict(document.metadata)
                required_strings = (
                    "collection_id",
                    "document_id",
                    "content_hash",
                    "filename",
                )
                required_ints = (
                    "owner_user_id",
                    "index_generation",
                    "chunk_index",
                    "source_start",
                    "source_end",
                )
                if (
                    not isinstance(document.page_content, str)
                    or not document.page_content.strip()
                    or any(
                        not isinstance(metadata.get(key), str) or not metadata[key]
                        for key in required_strings
                    )
                    or any(type(metadata.get(key)) is not int for key in required_ints)
                    or metadata["owner_user_id"] < 0
                    or metadata["index_generation"] < 1
                    or metadata["chunk_index"] < 0
                    or isinstance(score, bool)
                    or not isinstance(score, (int, float))
                ):
                    raise ValueError("invalid knowledge vector result")
                score_value = float(score)
                if (
                    not math.isfinite(score_value)
                    or score_value < -1.0 - _COSINE_SCORE_EPSILON
                    or score_value > 1.0 + _COSINE_SCORE_EPSILON
                ):
                    raise ValueError("invalid knowledge vector score")
                cosine_score = min(1.0, max(-1.0, score_value))
                normalized_score = (cosine_score + 1.0) / 2.0
                returned_scope = (
                    metadata["collection_id"],
                    metadata["owner_user_id"],
                    metadata["document_id"],
                    metadata["index_generation"],
                    metadata["content_hash"],
                )
                if returned_scope not in allowed:
                    raise ValueError("knowledge vector result escaped scope")
                normalized.append(
                    KnowledgeRetrieverHit(
                        content=document.page_content,
                        score=normalized_score,
                        metadata=metadata,
                        retrieval_mode="vector",
                        vector_score=normalized_score,
                    )
                )
            return normalized

        return self._external("normalize search results", normalize_results)

    def delete_generations_before(self, current: DocumentGeneration) -> None:
        exact_generation_conditions(current)
        if current.index_generation <= 1:
            return
        if not self._collection_exists():
            return
        self._external(
            "delete earlier generations",
            lambda: self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            *document_scope_conditions(current),
                            FieldCondition(
                                key="metadata.index_generation",
                                range=Range(lt=current.index_generation),
                            ),
                        ],
                    )
                ),
                wait=True,
            ),
        )

    def delete_generation(self, scope: DocumentGeneration) -> None:
        conditions = exact_generation_conditions(scope)
        if not self._collection_exists():
            return
        self._external(
            "delete generation",
            lambda: self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(must=conditions)
                ),
                wait=True,
            ),
        )

    def delete_document(self, scope: DocumentIndexScope) -> None:
        conditions = document_scope_conditions(scope)
        if not self._collection_exists():
            return
        self._external(
            "delete document",
            lambda: self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(must=conditions)
                ),
                wait=True,
            ),
        )

    def purge_collection(self, *, collection_id: str, owner_user_id: int) -> None:
        scope = DocumentIndexScope(
            collection_id=collection_id,
            owner_user_id=owner_user_id,
            document_id="purge-validation",
        )
        document_scope_conditions(scope)
        if not self._collection_exists():
            return
        self._external(
            "purge collection",
            lambda: self.client.delete(
                collection_name=self.collection_name,
                points_selector=FilterSelector(
                    filter=Filter(
                        must=[
                            FieldCondition(
                                key="metadata.collection_id",
                                match=MatchValue(value=collection_id),
                            ),
                            FieldCondition(
                                key="metadata.owner_user_id",
                                match=MatchValue(value=owner_user_id),
                            ),
                        ]
                    )
                ),
                wait=True,
            ),
        )

    def test_connection(self) -> bool:
        try:
            self.client.get_collections()
            return True
        except Exception:
            return False
