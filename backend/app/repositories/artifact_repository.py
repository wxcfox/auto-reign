from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.services.artifact_metadata import artifact_metadata_json, artifact_status_json


class ArtifactRepository:
    def get(self, session: Session, user_id: int, artifact_id: str) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.user_id == user_id,
                models.Artifact.id == artifact_id,
            )
        )

    def get_by_relative_path(
        self,
        session: Session,
        user_id: int,
        relative_path: str,
    ) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.user_id == user_id,
                models.Artifact.relative_path == relative_path,
            )
        )

    def get_source_by_content_hash(
        self,
        session: Session,
        user_id: int,
        content_hash: str,
    ) -> models.Artifact | None:
        return session.scalar(
            select(models.Artifact).where(
                models.Artifact.user_id == user_id,
                models.Artifact.kind == "source",
                models.Artifact.content_hash == content_hash,
            )
        )

    def list(self, session: Session, user_id: int) -> list[models.Artifact]:
        return list(
            session.scalars(
                select(models.Artifact)
                .where(models.Artifact.user_id == user_id)
                .order_by(models.Artifact.relative_path)
            )
        )

    def upsert(
        self,
        session: Session,
        *,
        user_id: int,
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
        artifact = self.get(session, user_id, artifact_id)
        if artifact is None:
            artifact = self.get_by_relative_path(session, user_id, relative_path)
        if artifact is None:
            artifact = models.Artifact(
                id=artifact_id,
                user_id=user_id,
                kind=kind,
                relative_path=relative_path,
            )
            session.add(artifact)

        artifact.user_id = user_id
        artifact.kind = kind
        artifact.relative_path = relative_path
        artifact.content_hash = content_hash
        artifact.revision = revision
        artifact.status_json = artifact_status_json(
            processing_status=processing_status,
            index_status=index_status,
            recovery_required=recovery_required,
            recovery_reason=recovery_reason,
        )
        artifact.metadata_json = artifact_metadata_json(
            source_refs=source_refs,
            evidence_refs=evidence_refs,
            language=language,
            source_filename=source_filename,
            media_type=media_type,
            size_bytes=size_bytes,
            origin=origin,
            edited_by=edited_by,
            uploaded_at=uploaded_at,
        )
        if created_at is not None:
            artifact.created_at = created_at
        if updated_at is not None:
            artifact.updated_at = updated_at
        session.flush()
        return artifact

    def delete_with_jobs(self, session: Session, artifact: models.Artifact) -> None:
        session.delete(artifact)
        session.flush()
