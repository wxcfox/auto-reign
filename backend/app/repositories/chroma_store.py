from dataclasses import dataclass
import math
from typing import Any

from app.core.config import Settings


@dataclass(frozen=True)
class ChromaChunk:
    id: str
    content: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool]


_MEMORY_STORES: dict[str, dict[str, dict[str, ChromaChunk]]] = {}


def _distance(left: list[float], right: list[float]) -> float:
    return math.sqrt(
        sum((value - query_value) ** 2 for value, query_value in zip(left, right, strict=True))
    )


class ChromaStore:
    def __init__(self, settings: Settings) -> None:
        if settings.qdrant_url != ":memory:":
            raise RuntimeError(
                "Qdrant storage is not configured until the Qdrant adapter task."
            )
        self._memory_collections = _MEMORY_STORES.setdefault(
            str(settings.data_dir.resolve()), {}
        )

    def upsert_chunks(self, collection_name: str, chunks: list[ChromaChunk]) -> None:
        if not chunks:
            return
        collection = self._memory_collections.setdefault(collection_name, {})
        collection.update({chunk.id: chunk for chunk in chunks})

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
        collection = self._memory_collections.setdefault(collection_name, {})
        chunk_ids = [
            chunk_id
            for chunk_id, chunk in collection.items()
            if chunk.metadata.get("document_id") == document_id
        ]
        for chunk_id in chunk_ids:
            del collection[chunk_id]

    def search(
        self, collection_name: str, query_embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        chunks = self._memory_collections.get(collection_name, {}).values()
        ranked_chunks = sorted(
            chunks, key=lambda chunk: _distance(chunk.embedding, query_embedding)
        )[:limit]
        return [
            {
                "content": chunk.content,
                "score": 1.0 / (1.0 + _distance(chunk.embedding, query_embedding)),
                "metadata": chunk.metadata,
            }
            for chunk in ranked_chunks
        ]
