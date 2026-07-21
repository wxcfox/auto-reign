from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from app.services.knowledge_chunk_service import KnowledgeChunk


RetrieverType = Literal["elasticsearch", "qdrant"]
RetrievalMode = Literal["vector", "keyword", "hybrid"]


@dataclass(frozen=True)
class KnowledgeRetrieverHit:
    content: str
    score: float
    metadata: dict[str, object]
    retrieval_mode: RetrievalMode = "vector"
    vector_score: float | None = None
    keyword_score: float | None = None
    fused_score: float | None = None


@dataclass(frozen=True)
class DocumentGeneration:
    collection_id: str
    owner_user_id: int
    document_id: str
    index_generation: int
    content_hash: str


@dataclass(frozen=True)
class DocumentIndexScope:
    collection_id: str
    owner_user_id: int
    document_id: str


class KnowledgeRetriever(Protocol):
    retriever_type: RetrieverType
    supported_retrieval_methods: frozenset[RetrievalMode]

    def upsert_generation(self, chunks: list[KnowledgeChunk]) -> None: ...

    def retrieve(
        self,
        query: str,
        *,
        scopes: list[DocumentGeneration],
        mode: RetrievalMode,
        limit: int,
        vector_weight: float,
        keyword_weight: float,
    ) -> list[KnowledgeRetrieverHit]: ...

    def delete_generations_before(self, current: DocumentGeneration) -> None: ...

    def delete_generation(self, scope: DocumentGeneration) -> None: ...

    def delete_document(self, scope: DocumentIndexScope) -> None: ...

    def purge_collection(self, *, collection_id: str, owner_user_id: int) -> None: ...

    def test_connection(self) -> bool: ...
