from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

try:
    import chromadb
except ModuleNotFoundError:
    chromadb = None


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
    def __init__(self, persist_path: Path) -> None:
        self._memory_collections = _MEMORY_STORES.setdefault(str(persist_path.resolve()), {})
        self.client: Any | None = None
        if chromadb is None:
            return

        from chromadb.config import Settings as ChromaSettings

        persist_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(persist_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def upsert_chunks(self, collection_name: str, chunks: list[ChromaChunk]) -> None:
        if not chunks:
            return
        if self.client is None:
            collection = self._memory_collections.setdefault(collection_name, {})
            collection.update({chunk.id: chunk for chunk in chunks})
            return
        collection = self.client.get_or_create_collection(collection_name)
        collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            embeddings=[chunk.embedding for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
        )

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
        if self.client is None:
            collection = self._memory_collections.setdefault(collection_name, {})
            chunk_ids = [
                chunk_id
                for chunk_id, chunk in collection.items()
                if chunk.metadata.get("document_id") == document_id
            ]
            for chunk_id in chunk_ids:
                del collection[chunk_id]
            return
        collection = self.client.get_or_create_collection(collection_name)
        collection.delete(where={"document_id": document_id})

    def search(
        self, collection_name: str, query_embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
        if self.client is None:
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
        collection = self.client.get_or_create_collection(collection_name)
        result = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            include=["documents", "distances", "metadatas"],
        )
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        hits: list[dict[str, Any]] = []
        for content, distance, metadata in zip(documents, distances, metadatas, strict=False):
            hits.append(
                {
                    "content": content,
                    "score": 1.0 / (1.0 + float(distance)),
                    "metadata": metadata or {},
                }
            )
        return hits
