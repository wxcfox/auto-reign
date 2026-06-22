from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import models
from app.schemas.workspace import ArtifactFrontMatter, SourceMeta


class UnsafeWorkspacePath(ValueError):
    """Raised when a requested workspace path escapes the workspace root."""


class WorkspaceService:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def initialize(self, *, language: str = "zh-CN") -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        for relative in (
            "sources/documents",
            "sources/extracted",
            "profile",
            "knowledge",
            "practice",
            "state",
            "reports",
            "archive",
            ".revisions",
        ):
            self.resolve_path(relative).mkdir(parents=True, exist_ok=True)
        manifest = self.resolve_path("workspace.md")
        if not manifest.exists():
            created_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
            manifest.write_text(
                "---\n"
                "schema_version: 1\n"
                f"language: {language}\n"
                f"created_at: '{created_at}'\n"
                "---\n\n"
                "# Auto Reign Workspace\n\n"
                "This directory is managed by Auto Reign. You can read and edit "
                "Markdown learning files, but do not store secrets here.\n",
                encoding="utf-8",
            )
        return self.root

    def resolve_path(self, relative_path: str | Path) -> Path:
        path = Path(relative_path)
        if path.is_absolute():
            raise UnsafeWorkspacePath(f"absolute paths are not allowed: {relative_path}")
        candidate = self.root / path
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise UnsafeWorkspacePath(f"path escapes workspace root: {relative_path}")
        return resolved

    def to_relative_path(self, path: str | Path) -> str:
        resolved = Path(path).resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise UnsafeWorkspacePath(f"path escapes workspace root: {path}")
        return resolved.relative_to(self.root).as_posix()

    def rebuild_projection(self, session: Session, repository, artifact_service) -> None:
        existing_by_path = {artifact.relative_path: artifact for artifact in repository.list(session)}
        scanned_paths: set[str] = set()

        for sidecar_path in sorted((self.root / "sources" / "documents").glob("*.meta.json")):
            source = SourceMeta.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
            source_path = self.resolve_path(source.relative_path)
            if not source_path.exists():
                continue
            scanned_paths.add(source.relative_path)
            repository.upsert(
                session,
                artifact_id=source.artifact_id,
                kind="source",
                relative_path=source.relative_path,
                content_hash=source.content_hash,
                revision=1,
                processing_status="completed",
                index_status=self._next_index_status(
                    existing_by_path.get(source.relative_path), source.content_hash
                ),
                language=source.language,
                source_filename=source.source_filename,
                media_type=source.media_type,
                size_bytes=source.size_bytes,
                origin="human",
                edited_by="user",
                uploaded_at=source.uploaded_at,
                created_at=source.uploaded_at,
                updated_at=source.uploaded_at,
            )

        for markdown_path in sorted(self.root.rglob("*.md")):
            relative_path = self.to_relative_path(markdown_path)
            kind = self._kind_for_markdown(relative_path)
            if kind is None:
                continue
            existing = existing_by_path.get(relative_path)
            try:
                document = artifact_service.parse_markdown(
                    markdown_path.read_text(encoding="utf-8")
                )
            except Exception:
                document = artifact_service.repair_markdown(
                    relative_path,
                    kind=kind,
                    existing_front_matter=self._front_matter_from_artifact(existing)
                    if existing is not None
                    else None,
                )

            scanned_paths.add(relative_path)
            content_hash = self._sha256(markdown_path.read_bytes())
            processing_status = (
                "needs_recovery" if document.front_matter.recovery_required else "completed"
            )
            index_status = "stale" if document.front_matter.recovery_required else self._next_index_status(
                existing, content_hash
            )
            repository.upsert(
                session,
                artifact_id=document.front_matter.id,
                kind=document.front_matter.kind,
                relative_path=relative_path,
                content_hash=content_hash,
                revision=document.front_matter.revision,
                source_refs=document.front_matter.source_refs,
                evidence_refs=document.front_matter.evidence_refs,
                processing_status=processing_status,
                index_status=index_status,
                language=document.front_matter.language,
                origin=document.front_matter.origin,
                edited_by=document.front_matter.edited_by,
                recovery_required=document.front_matter.recovery_required,
                recovery_reason=document.front_matter.recovery_reason,
                created_at=document.front_matter.created_at,
                updated_at=document.front_matter.updated_at,
            )

        for artifact in existing_by_path.values():
            if artifact.relative_path not in scanned_paths:
                repository.delete_with_jobs(session, artifact)

    def _kind_for_markdown(self, relative_path: str) -> str | None:
        parts = Path(relative_path).parts
        if relative_path == "workspace.md" or not parts or parts[0] == ".revisions":
            return None
        if relative_path == "profile/candidate.md":
            return "candidate_profile"
        if relative_path == "profile/target.md":
            return "target_profile"
        if relative_path == "state/mastery.md":
            return "mastery"
        if relative_path == "state/plan.md":
            return "plan"
        if parts[0] == "knowledge":
            return "knowledge"
        if parts[0] == "practice":
            return "practice"
        if parts[0] == "reports":
            return "report"
        if parts[:2] == ("sources", "extracted"):
            return "extracted"
        return None

    def _next_index_status(self, existing: models.Artifact | None, content_hash: str) -> str:
        if existing is None:
            return "pending"
        if existing.content_hash == content_hash:
            return existing.index_status
        return "stale"

    def _front_matter_from_artifact(
        self, artifact: models.Artifact | None
    ) -> ArtifactFrontMatter | None:
        if artifact is None:
            return None
        return ArtifactFrontMatter(
            id=artifact.id,
            kind=artifact.kind,  # type: ignore[arg-type]
            language=artifact.language,
            revision=artifact.revision,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
            source_refs=artifact.source_refs,
            evidence_refs=artifact.evidence_refs,
            origin=artifact.origin,  # type: ignore[arg-type]
            edited_by=artifact.edited_by,  # type: ignore[arg-type]
            recovery_required=artifact.recovery_required,
            recovery_reason=artifact.recovery_reason,
        )

    def _sha256(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
