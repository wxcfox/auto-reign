from collections.abc import Callable
from functools import lru_cache
from typing import Any, TypeVar

from qdrant_client import QdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    FilterSelector,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import get_settings
from app.repositories.vector_store import (
    VectorChunk,
    VectorDimensionMismatch,
    VectorSearchHit,
    VectorStoreError,
    VectorStoreUnavailable,
)


ResultT = TypeVar("ResultT")


def _call_qdrant(operation: Callable[..., ResultT], **kwargs: Any) -> ResultT:
    try:
        return operation(**kwargs)
    except VectorStoreError:
        raise
    except Exception as exc:
        operation_name = getattr(operation, "__name__", "operation")
        raise VectorStoreUnavailable(f"Qdrant client {operation_name} failed") from exc


class QdrantVectorStore:
    def __init__(self, client: QdrantClient) -> None:
        self._client = client

    def has_searchable_content(self, collection_name: str) -> bool:
        if not _call_qdrant(self._client.collection_exists, collection_name=collection_name):
            return False
        response = _call_qdrant(
            self._client.count,
            collection_name=collection_name,
            exact=False,
        )
        return int(response.count or 0) > 0

    def upsert_chunks(self, collection_name: str, chunks: list[VectorChunk]) -> None:
        if not chunks:
            return

        dimension = self._chunk_dimension(chunks)
        if _call_qdrant(self._client.collection_exists, collection_name=collection_name):
            stored_dimension = self._collection_dimension(collection_name)
            if stored_dimension != dimension:
                raise VectorDimensionMismatch(
                    f"Collection {collection_name!r} expects vectors of dimension "
                    f"{stored_dimension}, received {dimension}"
                )
        else:
            _call_qdrant(
                self._client.create_collection,
                collection_name=collection_name,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )

        points = [
            PointStruct(
                id=chunk.id,
                vector=chunk.embedding,
                payload={**chunk.metadata, "content": chunk.content},
            )
            for chunk in chunks
        ]
        _call_qdrant(
            self._client.upsert,
            collection_name=collection_name,
            points=points,
            wait=True,
        )

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
        if not _call_qdrant(self._client.collection_exists, collection_name=collection_name):
            return

        selector = FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id),
                    )
                ]
            )
        )
        _call_qdrant(
            self._client.delete,
            collection_name=collection_name,
            points_selector=selector,
            wait=True,
        )

    def search(
        self, collection_name: str, query_embedding: list[float], limit: int
    ) -> list[VectorSearchHit]:
        if not _call_qdrant(self._client.collection_exists, collection_name=collection_name):
            return []

        stored_dimension = self._collection_dimension(collection_name)
        query_dimension = len(query_embedding)
        if stored_dimension != query_dimension:
            raise VectorDimensionMismatch(
                f"Collection {collection_name!r} expects vectors of dimension "
                f"{stored_dimension}, received {query_dimension}"
            )

        response = _call_qdrant(
            self._client.query_points,
            collection_name=collection_name,
            query=query_embedding,
            limit=limit,
            with_payload=True,
        )
        hits: list[VectorSearchHit] = []
        for point in response.points:
            payload = dict(point.payload or {})
            content = str(payload.pop("content", ""))
            hits.append(
                VectorSearchHit(
                    content=content,
                    score=point.score,
                    metadata=payload,
                )
            )
        return hits

    def _collection_dimension(self, collection_name: str) -> int:
        collection = _call_qdrant(self._client.get_collection, collection_name=collection_name)
        vectors = collection.config.params.vectors
        if isinstance(vectors, dict):
            raise VectorDimensionMismatch(
                f"Collection {collection_name!r} does not use an unnamed vector"
            )
        return vectors.size

    @staticmethod
    def _chunk_dimension(chunks: list[VectorChunk]) -> int:
        dimensions = {len(chunk.embedding) for chunk in chunks}
        if 0 in dimensions:
            raise VectorDimensionMismatch("Chunk embeddings must not be empty")
        if len(dimensions) != 1:
            raise VectorDimensionMismatch("Chunk embeddings must use one dimension")
        return dimensions.pop()


@lru_cache
def get_qdrant_store() -> QdrantVectorStore:
    settings = get_settings()
    if settings.qdrant_url == ":memory:":
        client = _call_qdrant(QdrantClient, location=":memory:")
    else:
        client = _call_qdrant(QdrantClient, url=settings.qdrant_url)
    return QdrantVectorStore(client)
