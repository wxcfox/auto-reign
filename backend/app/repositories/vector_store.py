from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5


VectorMetadataValue = str | int | float | bool


@dataclass(frozen=True)
class VectorChunk:
    id: str
    content: str
    embedding: list[float]
    metadata: dict[str, VectorMetadataValue]


@dataclass(frozen=True)
class VectorSearchHit:
    content: str
    score: float
    metadata: dict[str, Any]


class VectorStoreError(Exception):
    pass


class VectorStoreUnavailable(VectorStoreError):
    pass


class VectorDimensionMismatch(VectorStoreError):
    pass


class VectorStore(Protocol):
    def upsert_chunks(self, collection_name: str, chunks: list[VectorChunk]) -> None: ...

    def delete_document_chunks(self, collection_name: str, document_id: str) -> None: ...

    def search(
        self, collection_name: str, query_embedding: list[float], limit: int
    ) -> list[VectorSearchHit]: ...


def stable_vector_id(source_type: str, source_id: str, chunk_index: int) -> str:
    name = f"auto-reign:{source_type}:{source_id}:{chunk_index}"
    return str(uuid5(NAMESPACE_URL, name))
