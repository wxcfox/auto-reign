from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.repositories.vector_store import VectorStoreUnavailable
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.workspace_vector_store import WorkspaceVectorStore, get_workspace_vector_store


class WorkspaceRetrievalService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: WorkspaceVectorStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or (
            WorkspaceVectorStore(settings=self.settings)
            if settings is not None
            else get_workspace_vector_store()
        )
        self.settings_repository = WorkspaceSettingsRepository()

    def search(self, session: Session, query: str, limit: int) -> list[dict[str, object]]:
        workspace_settings = self.settings_repository.get_or_create(session)
        collection = workspace_settings.active_collection or self.settings.qdrant_collection
        try:
            if not self.vector_store.has_searchable_content(collection):
                return []
            raw_hits = self.vector_store.search(collection, query, limit=limit)
        except VectorStoreUnavailable:
            return []
        return [
            {
                "content": hit.content,
                "score": hit.score,
                "source_type": str(hit.metadata.get("source_type", "artifact")),
                "source_id": str(hit.metadata.get("artifact_id") or hit.metadata.get("source_id") or ""),
                "artifact_kind": str(hit.metadata.get("artifact_kind", "")),
                "relative_path": str(hit.metadata.get("relative_path", "")),
            }
            for hit in raw_hits
        ]
