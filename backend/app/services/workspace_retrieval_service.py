from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.repositories.qdrant_store import get_qdrant_store
from app.repositories.vector_store import VectorDimensionMismatch, VectorStore, VectorStoreUnavailable
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.rag_service import RagService


class WorkspaceRetrievalService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: VectorStore | None = None,
        rag_service: RagService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or get_qdrant_store()
        self.rag_service = rag_service or RagService(settings=self.settings, vector_store=self.vector_store)
        self.settings_repository = WorkspaceSettingsRepository()

    def search(self, session: Session, query: str, limit: int) -> list[dict[str, object]]:
        workspace_settings = self.settings_repository.get_or_create(session)
        collection = workspace_settings.active_collection or self.settings.qdrant_collection
        if not self.vector_store.has_searchable_content(collection):
            return []
        query_embedding = self.rag_service.embed_texts([query])[0]
        try:
            raw_hits = self.vector_store.search(collection, query_embedding, limit)
        except (VectorDimensionMismatch, VectorStoreUnavailable):
            return []
        return [
            {
                "content": hit.content,
                "score": hit.score,
                "source_type": str(hit.metadata.get("source_type", "artifact")),
                "source_id": str(hit.metadata.get("artifact_id") or hit.metadata.get("source_id") or ""),
            }
            for hit in raw_hits
        ]
