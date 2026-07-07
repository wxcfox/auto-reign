from __future__ import annotations

import logging
import time
from collections.abc import Callable
from threading import Lock

from fastapi import HTTPException
from langchain_core.documents import Document
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.db import models
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.vector_store import VectorStoreError
from app.services.artifact_metadata import (
    artifact_index_status,
    artifact_media_type,
    artifact_processing_status,
    artifact_recovery_required,
    with_index_status,
)
from app.services.artifact_document_service import ArtifactDocumentBuilder, ArtifactTextSplitter
from app.services.artifact_service import ArtifactService, InvalidFrontMatter
from app.services.workspace_vector_store import WorkspaceVectorStore, get_workspace_vector_store
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)
_REBUILD_LOCK = Lock()


class IndexService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        vector_store: WorkspaceVectorStore | None = None,
        document_builder: ArtifactDocumentBuilder | None = None,
        text_splitter: ArtifactTextSplitter | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_store = vector_store or (
            WorkspaceVectorStore(settings=self.settings)
            if settings is not None
            else get_workspace_vector_store()
        )
        self.document_builder = document_builder or ArtifactDocumentBuilder()
        self.text_splitter = text_splitter or ArtifactTextSplitter()

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
            build = self._build_documents_for_artifact(artifact, workspace)
        except (OSError, UnicodeError, HTTPException, InvalidFrontMatter, RuntimeError):
            artifact.status_json = with_index_status(artifact, "stale")
            session.flush()
            return artifact

        if build.status == "stale":
            artifact.status_json = with_index_status(artifact, "stale")
            session.flush()
            return artifact
        try:
            if build.documents:
                self.vector_store.prepare_documents(build.documents)
            self.vector_store.delete_artifact_chunks(target_collection, artifact.id)
            if build.documents:
                self.vector_store.upsert_documents(target_collection, build.documents)
            artifact.status_json = with_index_status(artifact, "completed")
        except VectorStoreError:
            artifact.status_json = with_index_status(artifact, "stale")
        session.flush()
        return artifact

    def rebuild_index(
        self,
        session_factory: Callable[[], Session],
        workspace: WorkspaceService,
        artifact_repository: ArtifactRepository,
        *,
        user_id: int,
        qdrant_prefix: str,
    ) -> str:
        with _REBUILD_LOCK:
            return self._rebuild_index_unlocked(
                session_factory,
                workspace,
                artifact_repository,
                user_id=user_id,
                qdrant_prefix=qdrant_prefix,
            )

    def ensure_current(
        self,
        session_factory: Callable[[], Session],
        workspace: WorkspaceService,
        artifact_repository: ArtifactRepository,
        *,
        user_id: int,
        qdrant_prefix: str,
    ) -> str:
        with _REBUILD_LOCK:
            with session_factory() as session:
                artifacts = artifact_repository.list(session, user_id=user_id)
                active_collection = self._active_collection(session, user_id, qdrant_prefix)
                if not artifacts:
                    return active_collection
                if active_collection and all(
                    artifact_index_status(artifact) == "completed" for artifact in artifacts
                ):
                    return active_collection
            return self._rebuild_index_unlocked(
                session_factory,
                workspace,
                artifact_repository,
                user_id=user_id,
                qdrant_prefix=qdrant_prefix,
            )

    def _rebuild_index_unlocked(
        self,
        session_factory: Callable[[], Session],
        workspace: WorkspaceService,
        artifact_repository: ArtifactRepository,
        *,
        user_id: int,
        qdrant_prefix: str,
    ) -> str:
        new_collection = f"{qdrant_prefix}__{time.time_ns()}"
        completed_ids: list[str] = []
        stale_ids: list[str] = []
        old_collection = ""
        try:
            with session_factory() as session:
                old_collection = self._active_collection(session, user_id, qdrant_prefix)
                artifacts = artifact_repository.list(session, user_id=user_id)
                for artifact in artifacts:
                    try:
                        build = self._build_documents_for_artifact(artifact, workspace)
                    except (OSError, UnicodeError, HTTPException, InvalidFrontMatter, RuntimeError):
                        stale_ids.append(artifact.id)
                        continue
                    if build.status == "stale":
                        stale_ids.append(artifact.id)
                        continue
                    if build.documents:
                        try:
                            self.vector_store.upsert_documents(new_collection, build.documents)
                        except VectorStoreError as exc:
                            logger.info(
                                "Workspace artifact %s could not be indexed: %s",
                                artifact.id,
                                exc,
                            )
                            stale_ids.append(artifact.id)
                            continue
                    completed_ids.append(artifact.id)
        except Exception:
            self._delete_collection_best_effort(new_collection)
            raise

        try:
            with session_scope(session_factory) as session:
                for artifact_id in completed_ids:
                    artifact = artifact_repository.get(
                        session,
                        user_id=user_id,
                        artifact_id=artifact_id,
                    )
                    if artifact is not None:
                        artifact.status_json = with_index_status(artifact, "completed")
                for artifact_id in stale_ids:
                    artifact = artifact_repository.get(
                        session,
                        user_id=user_id,
                        artifact_id=artifact_id,
                    )
                    if artifact is not None:
                        artifact.status_json = with_index_status(artifact, "stale")
                self._set_active_collection(session, user_id, new_collection)
        except Exception:
            self._delete_collection_best_effort(new_collection)
            raise

        if old_collection and old_collection != new_collection:
            self._delete_collection_best_effort(old_collection)
        self.sweep_orphan_collections(
            session_factory,
            user_id=user_id,
            qdrant_prefix=qdrant_prefix,
        )
        return new_collection

    def sweep_orphan_collections(
        self,
        session_factory: Callable[[], Session],
        *,
        user_id: int,
        qdrant_prefix: str,
    ) -> None:
        with session_factory() as session:
            active_collection = self._active_collection(session, user_id, qdrant_prefix)
        prefix = f"{qdrant_prefix}__"
        for collection_name in self.vector_store.list_collections():
            if collection_name.startswith(prefix) and collection_name != active_collection:
                self._delete_collection_best_effort(collection_name)

    def _build_documents_for_artifact(
        self, artifact: models.Artifact, workspace: WorkspaceService
    ) -> "_BuildResult":
        if artifact_recovery_required(artifact) or artifact_processing_status(artifact) == "needs_recovery":
            return _BuildResult(status="stale", documents=[])
        text = self._read_indexable_text(artifact, workspace)
        if text is None:
            return _BuildResult(status="completed", documents=[])
        document = self.document_builder.build(artifact, text)
        documents = self.text_splitter.split([document])
        return _BuildResult(status="completed", documents=documents)

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
        if artifact.kind in {
            "knowledge",
            "question_bank",
            "project",
            "interview_record",
            "high_frequency",
            "practice",
        }:
            return ArtifactService(workspace).read_markdown(artifact.relative_path).body
        return None

    def _is_text_source(self, artifact: models.Artifact) -> bool:
        media_type = artifact_media_type(artifact) or ""
        if media_type in {"text/markdown", "text/plain"}:
            return True
        suffix = artifact.relative_path.rsplit(".", 1)[-1].lower()
        return suffix in {"md", "txt"}

    def _active_collection(self, session: Session, user_id: int, qdrant_prefix: str) -> str:
        user = session.get(models.User, user_id)
        if user is None:
            raise RuntimeError("user not found for index rebuild")
        active_collection = (user.settings_json or {}).get("active_collection")
        return active_collection if isinstance(active_collection, str) and active_collection else qdrant_prefix

    def _set_active_collection(self, session: Session, user_id: int, collection_name: str) -> None:
        user = session.get(models.User, user_id)
        if user is None:
            raise RuntimeError("user not found for index rebuild")
        user.settings_json = {**(user.settings_json or {}), "active_collection": collection_name}
        session.flush()

    def _delete_collection_best_effort(self, collection_name: str) -> None:
        try:
            self.vector_store.delete_collection(collection_name)
        except VectorStoreError:
            logger.warning("Failed to delete orphan vector collection %s", collection_name)


class _BuildResult:
    def __init__(self, *, status: str, documents: list[Document]) -> None:
        self.status = status
        self.documents = documents
