from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class ArtifactRepository:
    def get(self, session: Session, artifact_id: str) -> models.Artifact | None:
        return session.get(models.Artifact, artifact_id)

    def get_by_relative_path(self, session: Session, relative_path: str) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(models.Artifact.relative_path == relative_path)
        )

    def get_source_by_content_hash(self, session: Session, content_hash: str) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.kind == "source", models.Artifact.content_hash == content_hash
            )
        )

    def list(self, session: Session) -> list[models.Artifact]:
        return list(session.scalars(select(models.Artifact).order_by(models.Artifact.relative_path)))

    def upsert(
        self,
        session: Session,
        *,
        artifact_id: str,
        kind: str,
        relative_path: str,
        content_hash: str,
        revision: int,
        source_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        processing_status: str = "completed",
        index_status: str = "pending",
        language: str = "zh-CN",
        source_filename: str | None = None,
        media_type: str | None = None,
        size_bytes: int | None = None,
        origin: str = "llm",
        edited_by: str = "system",
        recovery_required: bool = False,
        recovery_reason: str | None = None,
        uploaded_at: datetime | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> models.Artifact:
        artifact = session.get(models.Artifact, artifact_id)
        if artifact is None:
            artifact = self.get_by_relative_path(session, relative_path)
        if artifact is None:
            artifact = models.Artifact(id=artifact_id, kind=kind, relative_path=relative_path)
            session.add(artifact)

        artifact.kind = kind
        artifact.relative_path = relative_path
        artifact.content_hash = content_hash
        artifact.revision = revision
        artifact.source_refs = source_refs or []
        artifact.evidence_refs = evidence_refs or []
        artifact.processing_status = processing_status
        artifact.index_status = index_status
        artifact.language = language
        artifact.source_filename = source_filename
        artifact.media_type = media_type
        artifact.size_bytes = size_bytes
        artifact.origin = origin
        artifact.edited_by = edited_by
        artifact.recovery_required = recovery_required
        artifact.recovery_reason = recovery_reason
        artifact.uploaded_at = uploaded_at
        if created_at is not None:
            artifact.created_at = created_at
        if updated_at is not None:
            artifact.updated_at = updated_at
        session.flush()
        return artifact

    def delete_with_jobs(self, session: Session, artifact: models.Artifact) -> None:
        for job in session.scalars(
            select(models.ProcessingJob).where(models.ProcessingJob.artifact_id == artifact.id)
        ):
            session.delete(job)
        session.delete(artifact)
        session.flush()
