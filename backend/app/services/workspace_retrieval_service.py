from __future__ import annotations

import logging

from qdrant_client.http.models import FieldCondition, Filter, MatchAny
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.repositories.vector_store import VectorStoreUnavailable
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.retrieval_postprocessor import RetrievalPostProcessor
from app.services.retrieval_query_planner import RetrievalQueryPlanner, RetrievalRequest
from app.services.workspace_vector_store import WorkspaceVectorStore, get_workspace_vector_store


logger = logging.getLogger(__name__)


class WorkspaceRetrievalService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: WorkspaceVectorStore | None = None,
        query_planner: RetrievalQueryPlanner | None = None,
        postprocessor: RetrievalPostProcessor | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or (
            WorkspaceVectorStore(settings=self.settings)
            if settings is not None
            else get_workspace_vector_store()
        )
        self.query_planner = query_planner or RetrievalQueryPlanner()
        self.postprocessor = postprocessor or RetrievalPostProcessor()
        self.settings_repository = WorkspaceSettingsRepository()

    def search(self, session: Session, request: RetrievalRequest) -> list[dict[str, object]]:
        workspace_settings = self.settings_repository.get_or_create(session)
        collection = workspace_settings.active_collection or self.settings.qdrant_collection
        try:
            if not self.vector_store.has_searchable_content(collection):
                return []
            plan = self.query_planner.plan(request)
            raw_hits = self.vector_store.search(
                collection,
                plan.semantic_query,
                limit=plan.candidate_limit,
                metadata_filter=self._metadata_filter(plan.artifact_kinds),
            )
            hits = self.postprocessor.process(raw_hits, plan)
        except VectorStoreUnavailable as exc:
            logger.info("Workspace retrieval unavailable: %s", exc)
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
            for hit in hits
        ]

    def _metadata_filter(self, artifact_kinds: tuple[str, ...]) -> Filter | None:
        if not artifact_kinds:
            return None
        return Filter(
            must=[
                FieldCondition(
                    key="metadata.artifact_kind",
                    match=MatchAny(any=list(artifact_kinds)),
                )
            ]
        )
