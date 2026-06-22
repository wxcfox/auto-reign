from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Lock

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import models
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.qdrant_store import get_qdrant_store
from app.repositories.vector_store import VectorChunk, VectorStore, VectorStoreError, stable_vector_id
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactService, InvalidFrontMatter
from app.services.rag_service import RagService
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)
_REBUILD_LOCK = Lock()


class IndexService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: VectorStore | None = None,
        embedder: RagService | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or get_qdrant_store()
        self.embedder = embedder or RagService(settings=self.settings, vector_store=self.vector_store)

    def index_artifact(
        self,
        session: Session,
        artifact: models.Artifact,
        workspace: WorkspaceService,
        *,
        collection_name: str | None = None,
    ) -> models.Artifact:
        target_collection = collection_name or self.settings.qdrant_collection
        try:
            build = self._build_chunks_for_artifact(artifact, workspace)
        except (OSError, UnicodeError, HTTPException, InvalidFrontMatter, RuntimeError):
            artifact.index_status = "stale"
            session.flush()
            return artifact

        if build.status == "stale":
            artifact.index_status = "stale"
            session.flush()
            return artifact
        try:
            self.vector_store.delete_document_chunks(target_collection, artifact.id)
            if build.chunks:
                self.vector_store.upsert_chunks(target_collection, build.chunks)
            artifact.index_status = "completed"
        except VectorStoreError:
            artifact.index_status = "stale"
        session.flush()
        return artifact

    def rebuild_index(
        self,
        session_factory: Callable[[], Session],
        workspace: WorkspaceService,
        artifact_repository: ArtifactRepository,
        *,
        settings_repository: WorkspaceSettingsRepository | None = None,
    ) -> str:
        with _REBUILD_LOCK:
            return self._rebuild_index_unlocked(
                session_factory,
                workspace,
                artifact_repository,
                settings_repository=settings_repository,
            )

    def ensure_current(
        self,
        session_factory: Callable[[], Session],
        workspace: WorkspaceService,
        artifact_repository: ArtifactRepository,
        *,
        settings_repository: WorkspaceSettingsRepository | None = None,
    ) -> str:
        settings_repository = settings_repository or WorkspaceSettingsRepository()
        with _REBUILD_LOCK:
            with session_factory() as session:
                artifacts = artifact_repository.list(session)
                settings = settings_repository.get_or_create(session)
                if not artifacts:
                    return settings.active_collection
                if settings.active_collection and all(
                    artifact.index_status == "completed" for artifact in artifacts
                ):
                    return settings.active_collection
            return self._rebuild_index_unlocked(
                session_factory,
                workspace,
                artifact_repository,
                settings_repository=settings_repository,
            )

    def _rebuild_index_unlocked(
        self,
        session_factory: Callable[[], Session],
        workspace: WorkspaceService,
        artifact_repository: ArtifactRepository,
        *,
        settings_repository: WorkspaceSettingsRepository | None = None,
    ) -> str:
        settings_repository = settings_repository or WorkspaceSettingsRepository()
        new_collection = f"{self.settings.qdrant_collection}__{time.time_ns()}"
        completed_ids: list[str] = []
        stale_ids: list[str] = []
        old_collection = ""
        try:
            with session_factory() as session:
                settings = settings_repository.get_or_create(session)
                old_collection = settings.active_collection
                artifacts = artifact_repository.list(session)
                for artifact in artifacts:
                    try:
                        build = self._build_chunks_for_artifact(artifact, workspace)
                    except (OSError, UnicodeError, HTTPException, InvalidFrontMatter, RuntimeError):
                        stale_ids.append(artifact.id)
                        continue
                    if build.status == "stale":
                        stale_ids.append(artifact.id)
                        continue
                    if build.chunks:
                        self.vector_store.upsert_chunks(new_collection, build.chunks)
                    completed_ids.append(artifact.id)
        except Exception:
            self._delete_collection_best_effort(new_collection)
            raise

        try:
            with session_scope(session_factory) as session:
                settings = settings_repository.get_or_create(session)
                for artifact_id in completed_ids:
                    artifact = artifact_repository.get(session, artifact_id)
                    if artifact is not None:
                        artifact.index_status = "completed"
                for artifact_id in stale_ids:
                    artifact = artifact_repository.get(session, artifact_id)
                    if artifact is not None:
                        artifact.index_status = "stale"
                settings.active_collection = new_collection
        except Exception:
            self._delete_collection_best_effort(new_collection)
            raise

        if old_collection and old_collection != new_collection:
            self._delete_collection_best_effort(old_collection)
        self.sweep_orphan_collections(session_factory, settings_repository=settings_repository)
        return new_collection

    def sweep_orphan_collections(
        self,
        session_factory: Callable[[], Session],
        *,
        settings_repository: WorkspaceSettingsRepository | None = None,
    ) -> None:
        settings_repository = settings_repository or WorkspaceSettingsRepository()
        with session_factory() as session:
            active_collection = settings_repository.get_or_create(session).active_collection
        prefix = f"{self.settings.qdrant_collection}__"
        for collection_name in self.vector_store.list_collections():
            if collection_name.startswith(prefix) and collection_name != active_collection:
                self._delete_collection_best_effort(collection_name)

    def _build_chunks_for_artifact(
        self, artifact: models.Artifact, workspace: WorkspaceService
    ) -> "_BuildResult":
        if artifact.recovery_required or artifact.processing_status == "needs_recovery":
            return _BuildResult(status="stale", chunks=[])
        text = self._read_indexable_text(artifact, workspace)
        if text is None:
            return _BuildResult(status="completed", chunks=[])
        chunk_texts = self.embedder.split_text(text)
        embeddings = self.embedder.embed_texts(chunk_texts)
        chunks = [
            VectorChunk(
                id=stable_vector_id("artifact", artifact.id, index),
                content=chunk,
                embedding=embedding,
                metadata={
                    "source_type": "artifact",
                    "document_id": artifact.id,
                    "source_id": artifact.id,
                    "artifact_id": artifact.id,
                    "artifact_kind": artifact.kind,
                    "relative_path": artifact.relative_path,
                    "chunk_index": index,
                    "source_refs": artifact.source_refs,
                    "evidence_refs": artifact.evidence_refs,
                },
            )
            for index, (chunk, embedding) in enumerate(zip(chunk_texts, embeddings, strict=True))
        ]
        return _BuildResult(status="completed", chunks=chunks)

    def _read_indexable_text(
        self, artifact: models.Artifact, workspace: WorkspaceService
    ) -> str | None:
        path = workspace.resolve_path(artifact.relative_path)
        if artifact.kind == "source":
            if not self._is_text_source(artifact):
                return None
            return path.read_text(encoding="utf-8")
        if artifact.kind == "extracted":
            return path.read_text(encoding="utf-8")
        if artifact.kind in {"knowledge", "practice"}:
            return ArtifactService(workspace).read_markdown(artifact.relative_path).body
        return None

    def _is_text_source(self, artifact: models.Artifact) -> bool:
        media_type = artifact.media_type or ""
        if media_type in {"text/markdown", "text/plain"}:
            return True
        suffix = artifact.relative_path.rsplit(".", 1)[-1].lower()
        return suffix in {"md", "txt"}

    def _delete_collection_best_effort(self, collection_name: str) -> None:
        try:
            self.vector_store.delete_collection(collection_name)
        except VectorStoreError:
            logger.warning("Failed to delete orphan vector collection %s", collection_name)


class _BuildResult:
    def __init__(self, *, status: str, chunks: list[VectorChunk]) -> None:
        self.status = status
        self.chunks = chunks
