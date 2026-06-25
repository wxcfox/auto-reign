from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    VectorParams,
)

from app.core.config import Settings, get_settings
from app.repositories.vector_store import VectorStoreUnavailable, stable_vector_id
from app.services.embedding_service import EmbeddingService


@dataclass(frozen=True)
class WorkspaceVectorHit:
    content: str
    score: float
    metadata: dict[str, Any]


class WorkspaceVectorStore:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: QdrantClient | None = None,
        embeddings: Embeddings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client = client or self._build_client()
        self.embeddings = embeddings or EmbeddingService(self.settings).embeddings

    def upsert_documents(self, collection_name: str, documents: list[Document]) -> None:
        if not documents:
            return

        ids = [self._document_id(document) for document in documents]
        try:
            self._ensure_collection(collection_name, documents)
            self._vector_store(collection_name).add_documents(documents=documents, ids=ids)
        except VectorStoreUnavailable:
            raise
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant LangChain upsert failed") from exc

    def search(
        self,
        collection_name: str,
        query: str,
        *,
        limit: int,
        metadata_filter: Filter | None = None,
    ) -> list[WorkspaceVectorHit]:
        if not self.has_searchable_content(collection_name):
            return []

        try:
            results = self._vector_store(collection_name).similarity_search_with_score(
                query,
                k=limit,
                filter=metadata_filter,
            )
        except VectorStoreUnavailable:
            raise
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant LangChain search failed") from exc

        return [
            WorkspaceVectorHit(
                content=document.page_content,
                score=float(score),
                metadata=dict(document.metadata or {}),
            )
            for document, score in results
        ]

    def delete_artifact_chunks(self, collection_name: str, artifact_id: str) -> None:
        if not self._collection_exists(collection_name):
            return

        selector = FilterSelector(
            filter=Filter(
                must=[
                    FieldCondition(
                        key="metadata.artifact_id",
                        match=MatchValue(value=artifact_id),
                    )
                ]
            )
        )
        try:
            self.client.delete(
                collection_name=collection_name,
                points_selector=selector,
                wait=True,
            )
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant delete artifact chunks failed") from exc

    def delete_collection(self, collection_name: str) -> None:
        if not self._collection_exists(collection_name):
            return

        try:
            self.client.delete_collection(collection_name=collection_name)
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant delete collection failed") from exc

    def list_collections(self) -> list[str]:
        try:
            response = self.client.get_collections()
            return [collection.name for collection in response.collections]
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant list collections failed") from exc

    def has_searchable_content(self, collection_name: str) -> bool:
        if not self._collection_exists(collection_name):
            return False

        try:
            response = self.client.count(collection_name=collection_name, exact=False)
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant count failed") from exc
        return int(response.count or 0) > 0

    def _vector_store(self, collection_name: str) -> QdrantVectorStore:
        return QdrantVectorStore(
            client=self.client,
            collection_name=collection_name,
            embedding=self.embeddings,
        )

    def _build_client(self) -> QdrantClient:
        try:
            if self.settings.qdrant_url == ":memory:":
                return QdrantClient(location=":memory:")
            return QdrantClient(url=self.settings.qdrant_url)
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant client construction failed") from exc

    def _collection_exists(self, collection_name: str) -> bool:
        try:
            return bool(self.client.collection_exists(collection_name=collection_name))
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant collection_exists failed") from exc

    def _ensure_collection(self, collection_name: str, documents: list[Document]) -> None:
        if self._collection_exists(collection_name):
            return

        probe_text = next(
            (document.page_content for document in documents if document.page_content),
            "dimension probe",
        )
        dimension = len(self.embeddings.embed_documents([probe_text])[0])
        try:
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
            )
        except Exception as exc:
            raise VectorStoreUnavailable("Qdrant create collection failed") from exc

    @staticmethod
    def _document_id(document: Document) -> str:
        metadata = document.metadata or {}
        source_id = metadata.get("artifact_id") or metadata.get("source_id") or "artifact"
        chunk_index = int(metadata.get("chunk_index", 0))
        return stable_vector_id("artifact", str(source_id), chunk_index)
