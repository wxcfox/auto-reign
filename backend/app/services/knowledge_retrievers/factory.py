from __future__ import annotations

from collections.abc import Mapping

from app.core.config import Settings, get_settings
from app.services.knowledge_retrievers.base import (
    DocumentIndexScope,
    KnowledgeRetriever,
    RetrieverType,
)
from app.services.knowledge_retrievers.elasticsearch import ElasticsearchRetriever
from app.services.knowledge_retrievers.qdrant import QdrantRetriever


class KnowledgeRetrieverFactory:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        retrievers: Mapping[RetrieverType, KnowledgeRetriever] | None = None,
    ) -> None:
        resolved_settings = settings or get_settings()
        self._retrievers: dict[RetrieverType, KnowledgeRetriever] = (
            dict(retrievers)
            if retrievers is not None
            else {
                "elasticsearch": ElasticsearchRetriever(settings=resolved_settings),
                "qdrant": QdrantRetriever(settings=resolved_settings),
            }
        )
        if set(self._retrievers) != {"elasticsearch", "qdrant"}:
            raise ValueError("Elasticsearch and Qdrant retrievers are both required")
        for retriever_type, retriever in self._retrievers.items():
            if retriever.retriever_type != retriever_type:
                raise ValueError("knowledge retriever type does not match factory key")

    def get(self, retriever_type: RetrieverType) -> KnowledgeRetriever:
        try:
            return self._retrievers[retriever_type]
        except KeyError as error:
            raise ValueError(f"Unsupported knowledge retriever: {retriever_type}") from error

    def supported_retrieval_methods(self, retriever_type: RetrieverType) -> tuple[str, ...]:
        return tuple(sorted(self.get(retriever_type).supported_retrieval_methods))

    def test_connections(self) -> dict[RetrieverType, bool]:
        return {
            retriever_type: retriever.test_connection()
            for retriever_type, retriever in self._retrievers.items()
        }

    def all(self) -> tuple[KnowledgeRetriever, ...]:
        return tuple(self._retrievers.values())

    def delete_document(self, scope: DocumentIndexScope) -> None:
        errors: list[Exception] = []
        for retriever in self.all():
            try:
                retriever.delete_document(scope)
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def purge_collection(self, *, collection_id: str, owner_user_id: int) -> None:
        errors: list[Exception] = []
        for retriever in self.all():
            try:
                retriever.purge_collection(
                    collection_id=collection_id,
                    owner_user_id=owner_user_id,
                )
            except Exception as error:
                errors.append(error)
        if errors:
            raise errors[0]
