from dataclasses import dataclass
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings


@dataclass(frozen=True)
class ChromaChunk:
    id: str
    content: str
    embedding: list[float]
    metadata: dict[str, str | int | float | bool]


class ChromaStore:
    def __init__(self, persist_path: Path) -> None:
        persist_path.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=str(persist_path),
            settings=ChromaSettings(anonymized_telemetry=False),
        )

    def upsert_chunks(self, collection_name: str, chunks: list[ChromaChunk]) -> None:
        if not chunks:
            return
        collection = self.client.get_or_create_collection(collection_name)
        collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            embeddings=[chunk.embedding for chunk in chunks],
            metadatas=[chunk.metadata for chunk in chunks],
        )

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None:
        collection = self.client.get_or_create_collection(collection_name)
        collection.delete(where={"document_id": document_id})

    def search(
        self, collection_name: str, query_embedding: list[float], limit: int
    ) -> list[dict[str, Any]]:
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
