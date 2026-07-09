from __future__ import annotations

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.default_manifest import read_default_manifest_body
from app.db import models
from app.schemas.workspace import ArtifactFrontMatter, SourceMeta
from app.services.artifact_metadata import (
    artifact_edited_by,
    artifact_evidence_refs,
    artifact_index_status,
    artifact_language,
    artifact_origin,
    artifact_recovery_reason,
    artifact_recovery_required,
    artifact_source_refs,
)
from app.services.workspace_paths import (
    CANDIDATE_PROFILE_PATH,
    EXTRACTED_SOURCE_DIR,
    HIGH_FREQUENCY_PATH,
    MANIFEST_PATH,
    MASTERY_PATH,
    RAW_SOURCE_DIR,
    REVIEW_STATUS_PATH,
    SOURCE_SIDE_CAR_DIRECTORIES,
    TARGET_PROFILE_PATH,
    WORKSPACE_DIRECTORIES,
)


class UnsafeWorkspacePath(ValueError):
    """Raised when a requested workspace path escapes the workspace root."""


class WorkspaceService:
    def __init__(self, root: Path, *, default_manifest_path: Path | None = None) -> None:
        self.root = root.resolve()
        self.default_manifest_path = (
            default_manifest_path.resolve(strict=False)
            if default_manifest_path is not None
            else None
        )

    def initialize(self, *, language: str = "zh-CN") -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        for relative in WORKSPACE_DIRECTORIES:
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
        self._initialize_manifest(language=language)
        return self.root

    def _initialize_manifest(self, *, language: str) -> None:
        manifest_path = self.resolve_path(MANIFEST_PATH)
        default_body = read_default_manifest_body(self.default_manifest_path)
        timestamp = datetime.now(UTC)
        if not manifest_path.exists():
            front_matter = ArtifactFrontMatter(
                id=str(uuid4()),
                kind="manifest",
                language=language,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                source_refs=[],
                evidence_refs=[],
                origin="human",
                edited_by="system",
                recovery_required=False,
                recovery_reason=None,
            )
            manifest_path.write_text(
                self._serialize_markdown(front_matter, default_body),
                encoding="utf-8",
            )
            return

        raw = manifest_path.read_text(encoding="utf-8")
        try:
            front_matter, body = self._parse_markdown(raw)
        except ValueError:
            return
        if front_matter.kind != "manifest" or front_matter.edited_by != "system":
            return
        if body == default_body:
            return

        self._save_raw_revision(front_matter.id, front_matter.revision, raw)
        updated_front_matter = front_matter.model_copy(
            update={
                "revision": front_matter.revision + 1,
                "updated_at": timestamp,
                "edited_by": "system",
            }
        )
        manifest_path.write_text(
            self._serialize_markdown(updated_front_matter, default_body),
            encoding="utf-8",
        )

    def _parse_markdown(self, raw: str) -> tuple[ArtifactFrontMatter, str]:
        if not raw.startswith("---\n"):
            raise ValueError("managed Markdown must start with YAML front matter")
        marker = "\n---\n"
        end = raw.find(marker, 4)
        if end == -1:
            raise ValueError("front matter closing marker is missing")
        front_matter_text = raw[4:end]
        body = raw[end + len(marker) :]
        try:
            data = yaml.safe_load(front_matter_text) or {}
            front_matter = ArtifactFrontMatter.model_validate(data)
        except (TypeError, ValueError, ValidationError, yaml.YAMLError) as exc:
            raise ValueError("front matter is not valid workspace metadata") from exc
        return front_matter, body

    def _serialize_markdown(self, front_matter: ArtifactFrontMatter, body: str) -> str:
        data = front_matter.model_dump(mode="python")
        data["created_at"] = self._format_datetime(front_matter.created_at)
        data["updated_at"] = self._format_datetime(front_matter.updated_at)
        yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{yaml_text}\n---\n{body}"

    def _save_raw_revision(self, artifact_id: str, revision: int, raw: str) -> Path:
        revision_dir = self.resolve_path(f".revisions/{artifact_id}")
        revision_dir.mkdir(parents=True, exist_ok=True)
        revision_path = revision_dir / f"{time.time_ns()}-r{revision}.md"
        revision_path.write_text(raw, encoding="utf-8")
        return revision_path

    def _format_datetime(self, value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

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

    def rebuild_projection(
        self,
        session: Session,
        repository,
        artifact_service,
        *,
        user_id: int,
    ) -> None:
        existing_by_path = {
            artifact.relative_path: artifact for artifact in repository.list(session, user_id=user_id)
        }
        scanned_paths: set[str] = set()
        source_relative_paths: set[str] = set()

        for source_dir in (self.root / relative for relative in SOURCE_SIDE_CAR_DIRECTORIES):
            for sidecar_path in sorted(source_dir.glob("*.meta.json")):
                source = SourceMeta.model_validate_json(sidecar_path.read_text(encoding="utf-8"))
                source_path = self.resolve_path(source.relative_path)
                if not source_path.exists():
                    continue
                scanned_paths.add(source.relative_path)
                source_relative_paths.add(source.relative_path)
                repository.upsert(
                    session,
                    user_id=user_id,
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
                    source_type=source.source_type,
                    origin="human",
                    edited_by="user",
                    uploaded_at=source.uploaded_at,
                    created_at=source.uploaded_at,
                    updated_at=source.uploaded_at,
                )

        for markdown_path in sorted(self.root.rglob("*.md")):
            relative_path = self.to_relative_path(markdown_path)
            if relative_path in source_relative_paths:
                continue
            kind = self._kind_for_markdown(relative_path)
            parsed_document = None
            if kind is None and self._is_raw_managed_markdown(relative_path):
                try:
                    parsed_document = artifact_service.parse_markdown(
                        markdown_path.read_text(encoding="utf-8")
                    )
                except Exception:
                    continue
                kind = parsed_document.front_matter.kind
            if kind is None:
                continue
            existing = existing_by_path.get(relative_path)
            try:
                document = parsed_document or artifact_service.parse_markdown(
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
                user_id=user_id,
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
        if relative_path == MANIFEST_PATH:
            return "manifest"
        if relative_path == CANDIDATE_PROFILE_PATH:
            return "candidate_profile"
        if relative_path == TARGET_PROFILE_PATH:
            return "target_profile"
        if relative_path == MASTERY_PATH:
            return "mastery"
        if relative_path == "state/plan.md":
            return "plan"
        if parts[0] == "knowledge":
            return "knowledge"
        if parts[0] == "questions":
            return "question_bank"
        if parts[0] == "projects":
            return "project"
        if relative_path == HIGH_FREQUENCY_PATH:
            return "high_frequency"
        if relative_path == REVIEW_STATUS_PATH:
            return "review_status"
        if parts[0] == "practice":
            return "practice"
        if parts[0] == "reports":
            return "report"
        if parts[0] == EXTRACTED_SOURCE_DIR:
            return "extracted"
        return None

    def _is_raw_managed_markdown(self, relative_path: str) -> bool:
        parts = Path(relative_path).parts
        return len(parts) == 2 and parts[0] == RAW_SOURCE_DIR

    def _next_index_status(self, existing: models.Artifact | None, content_hash: str) -> str:
        if existing is None:
            return "pending"
        if existing.content_hash == content_hash:
            return artifact_index_status(existing)
        return "stale"

    def _front_matter_from_artifact(
        self, artifact: models.Artifact | None
    ) -> ArtifactFrontMatter | None:
        if artifact is None:
            return None
        return ArtifactFrontMatter(
            id=artifact.id,
            kind=artifact.kind,  # type: ignore[arg-type]
            language=artifact_language(artifact),
            revision=artifact.revision,
            created_at=artifact.created_at,
            updated_at=artifact.updated_at,
            source_refs=artifact_source_refs(artifact),
            evidence_refs=artifact_evidence_refs(artifact),
            origin=artifact_origin(artifact),  # type: ignore[arg-type]
            edited_by=artifact_edited_by(artifact),  # type: ignore[arg-type]
            recovery_required=artifact_recovery_required(artifact),
            recovery_reason=artifact_recovery_reason(artifact),
        )

    def _sha256(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()
