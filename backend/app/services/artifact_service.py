from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from pydantic import ValidationError

from app.schemas.workspace import ArtifactFrontMatter, ArtifactKind, EditedBy, Origin, SourceMeta
from app.services.workspace_service import WorkspaceService


class ArtifactConflict(ValueError):
    """Raised when an update attempts to overwrite a newer revision."""


class InvalidFrontMatter(ValueError):
    """Raised when a managed Markdown file is missing valid front matter."""


@dataclass(frozen=True)
class ManagedMarkdown:
    front_matter: ArtifactFrontMatter
    body: str
    raw: str


class ArtifactService:
    def __init__(self, workspace: WorkspaceService, *, revisions_retained: int = 20) -> None:
        self.workspace = workspace
        self.revisions_retained = revisions_retained

    def create_markdown(
        self,
        relative_path: str,
        *,
        kind: ArtifactKind,
        body: str,
        language: str = "zh-CN",
        source_refs: list[str] | None = None,
        evidence_refs: list[str] | None = None,
        origin: Origin = "llm",
        edited_by: EditedBy = "system",
        now: datetime | None = None,
        artifact_id: str | None = None,
        recovery_required: bool = False,
        recovery_reason: str | None = None,
    ) -> ManagedMarkdown:
        timestamp = self._coerce_utc(now or datetime.now(UTC))
        front_matter = ArtifactFrontMatter(
            id=artifact_id or str(uuid4()),
            kind=kind,
            language=language,
            revision=1,
            created_at=timestamp,
            updated_at=timestamp,
            source_refs=source_refs or [],
            evidence_refs=evidence_refs or [],
            origin=origin,
            edited_by=edited_by,
            recovery_required=recovery_required,
            recovery_reason=recovery_reason,
        )
        raw = self.serialize_markdown(front_matter, body)
        self.atomic_write_bytes(self.workspace.resolve_path(relative_path), raw.encode("utf-8"))
        return ManagedMarkdown(front_matter=front_matter, body=body, raw=raw)

    def read_markdown(self, relative_path: str) -> ManagedMarkdown:
        return self.parse_markdown(
            self.workspace.resolve_path(relative_path).read_text(encoding="utf-8")
        )

    def parse_markdown(self, raw: str) -> ManagedMarkdown:
        if not raw.startswith("---\n"):
            raise InvalidFrontMatter("managed Markdown must start with YAML front matter")
        marker = "\n---\n"
        end = raw.find(marker, 4)
        if end == -1:
            raise InvalidFrontMatter("front matter closing marker is missing")
        front_matter_text = raw[4:end]
        body = raw[end + len(marker) :]
        try:
            data = yaml.safe_load(front_matter_text) or {}
            front_matter = ArtifactFrontMatter.model_validate(data)
        except (TypeError, ValueError, ValidationError, yaml.YAMLError) as exc:
            raise InvalidFrontMatter("front matter is not valid workspace metadata") from exc
        return ManagedMarkdown(front_matter=front_matter, body=body, raw=raw)

    def serialize_markdown(self, front_matter: ArtifactFrontMatter, body: str) -> str:
        data = front_matter.model_dump(mode="python")
        data["created_at"] = self._format_datetime(front_matter.created_at)
        data["updated_at"] = self._format_datetime(front_matter.updated_at)
        yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
        return f"---\n{yaml_text}\n---\n{body}"

    def update_sections(
        self,
        relative_path: str,
        *,
        expected_revision: int,
        sections: dict[str, str],
        edited_by: EditedBy = "system",
        now: datetime | None = None,
    ) -> ManagedMarkdown:
        path = self.workspace.resolve_path(relative_path)
        current = self.parse_markdown(path.read_text(encoding="utf-8"))
        if current.front_matter.revision != expected_revision:
            raise ArtifactConflict(
                f"expected revision {expected_revision}, found {current.front_matter.revision}"
            )

        self.save_revision(current)
        body = current.body
        for heading, content in sections.items():
            body = self._replace_h2_section(body, heading, content)
        updated_front_matter = current.front_matter.model_copy(
            update={
                "revision": current.front_matter.revision + 1,
                "updated_at": self._coerce_utc(now or datetime.now(UTC)),
                "edited_by": edited_by,
            }
        )
        raw = self.serialize_markdown(updated_front_matter, body)
        self.atomic_write_bytes(path, raw.encode("utf-8"))
        self.prune_revisions(current.front_matter.id)
        return ManagedMarkdown(front_matter=updated_front_matter, body=body, raw=raw)

    def replace_body(
        self,
        relative_path: str,
        *,
        expected_revision: int,
        body: str,
        edited_by: EditedBy = "user",
        now: datetime | None = None,
    ) -> ManagedMarkdown:
        path = self.workspace.resolve_path(relative_path)
        current = self.parse_markdown(path.read_text(encoding="utf-8"))
        if current.front_matter.revision != expected_revision:
            raise ArtifactConflict(
                f"expected revision {expected_revision}, found {current.front_matter.revision}"
            )
        self.save_revision(current)
        updated_front_matter = current.front_matter.model_copy(
            update={
                "revision": current.front_matter.revision + 1,
                "updated_at": self._coerce_utc(now or datetime.now(UTC)),
                "edited_by": edited_by,
            }
        )
        raw = self.serialize_markdown(updated_front_matter, body)
        self.atomic_write_bytes(path, raw.encode("utf-8"))
        self.prune_revisions(current.front_matter.id)
        return ManagedMarkdown(front_matter=updated_front_matter, body=body, raw=raw)

    def save_revision(self, document: ManagedMarkdown) -> Path:
        return self.save_raw_revision(
            document.front_matter.id, document.front_matter.revision, document.raw
        )

    def save_raw_revision(self, artifact_id: str, revision: int, raw: str) -> Path:
        revision_dir = self.workspace.resolve_path(f".revisions/{artifact_id}")
        revision_dir.mkdir(parents=True, exist_ok=True)
        revision_path = revision_dir / f"{time.time_ns()}-r{revision}.md"
        self.atomic_write_bytes(revision_path, raw.encode("utf-8"))
        return revision_path

    def prune_revisions(self, artifact_id: str) -> None:
        revision_dir = self.workspace.resolve_path(f".revisions/{artifact_id}")
        if not revision_dir.exists():
            return
        revisions = sorted(
            revision_dir.glob("*.md"), key=lambda path: (path.stat().st_mtime_ns, path.name)
        )
        for old_revision in revisions[: max(0, len(revisions) - self.revisions_retained)]:
            old_revision.unlink()

    def repair_markdown(
        self,
        relative_path: str,
        *,
        kind: ArtifactKind,
        existing_front_matter: ArtifactFrontMatter | None = None,
        language: str = "zh-CN",
        reason: str = "front matter missing or invalid",
        now: datetime | None = None,
    ) -> ManagedMarkdown:
        path = self.workspace.resolve_path(relative_path)
        raw = path.read_text(encoding="utf-8")
        body = self.body_without_front_matter(raw)
        timestamp = self._coerce_utc(now or datetime.now(UTC))
        if existing_front_matter is None:
            front_matter = ArtifactFrontMatter(
                id=str(uuid4()),
                kind=kind,
                language=language,
                revision=1,
                created_at=timestamp,
                updated_at=timestamp,
                source_refs=[],
                evidence_refs=[],
                origin="human",
                edited_by="user",
                recovery_required=True,
                recovery_reason=reason,
            )
            saved_revision = 0
        else:
            front_matter = existing_front_matter.model_copy(
                update={
                    "kind": kind,
                    "revision": existing_front_matter.revision + 1,
                    "updated_at": timestamp,
                    "edited_by": "user",
                    "recovery_required": True,
                    "recovery_reason": reason,
                }
            )
            saved_revision = existing_front_matter.revision
        self.save_raw_revision(front_matter.id, saved_revision, raw)
        repaired_raw = self.serialize_markdown(front_matter, body)
        self.atomic_write_bytes(path, repaired_raw.encode("utf-8"))
        self.prune_revisions(front_matter.id)
        return ManagedMarkdown(front_matter=front_matter, body=body, raw=repaired_raw)

    def body_without_front_matter(self, raw: str) -> str:
        if not raw.startswith("---\n"):
            return raw
        marker = "\n---\n"
        end = raw.find(marker, 4)
        if end == -1:
            return raw
        return raw[end + len(marker) :]

    def store_source(
        self,
        *,
        source_filename: str,
        media_type: str,
        content: bytes,
        language: str = "zh-CN",
        artifact_id: str | None = None,
        uploaded_at: datetime | None = None,
    ) -> SourceMeta:
        source_id = artifact_id or str(uuid4())
        actual_name = f"{source_id}-{self._sanitize_filename(source_filename)}"
        documents_dir = self.workspace.resolve_path("sources/documents")
        if artifact_id is not None and any(documents_dir.glob(f"{source_id}-*")):
            raise FileExistsError(source_id)
        path = self.workspace.resolve_path(f"sources/documents/{actual_name}")
        if path.exists():
            raise FileExistsError(path)

        content_hash = hashlib.sha256(content).hexdigest()
        timestamp = self._coerce_utc(uploaded_at or datetime.now(UTC))
        meta = SourceMeta(
            artifact_id=source_id,
            source_filename=source_filename,
            media_type=media_type,
            size_bytes=len(content),
            content_hash=content_hash,
            uploaded_at=timestamp,
            relative_path=self.workspace.to_relative_path(path),
            language=language,
        )
        self.atomic_write_bytes(path, content)
        sidecar_path = path.with_name(f"{path.name}.meta.json")
        self.atomic_write_bytes(
            sidecar_path,
            json.dumps(self._json_ready(meta.model_dump()), ensure_ascii=False, indent=2)
            .encode("utf-8"),
        )
        return meta

    def atomic_write_bytes(self, path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            with temp_path.open("wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def _replace_h2_section(self, body: str, heading: str, content: str) -> str:
        normalized = content.strip()
        replacement = f"## {heading}\n\n{normalized}\n"
        pattern = re.compile(
            rf"^##[ \t]+{re.escape(heading)}[ \t]*\n.*?(?=^##[ \t]+|\Z)",
            flags=re.DOTALL | re.MULTILINE,
        )
        if pattern.search(body):
            return pattern.sub(replacement, body, count=1)
        prefix = body.rstrip()
        return f"{prefix}\n\n{replacement}" if prefix else replacement

    def _sanitize_filename(self, source_filename: str) -> str:
        basename = Path(source_filename).name or "source"
        basename = re.sub(r"[^A-Za-z0-9._-]+", "-", basename).strip(".-_")
        return basename or "source"

    def _format_datetime(self, value: datetime) -> str:
        return self._coerce_utc(value).isoformat().replace("+00:00", "Z")

    def _coerce_utc(self, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _json_ready(self, value: Any) -> Any:
        if isinstance(value, datetime):
            return self._format_datetime(value)
        if isinstance(value, dict):
            return {key: self._json_ready(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._json_ready(item) for item in value]
        return value
