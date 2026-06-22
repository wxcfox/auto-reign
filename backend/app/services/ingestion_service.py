from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import models
from app.repositories.artifact_repository import ArtifactRepository
from app.services.artifact_service import ArtifactService
from app.services.extraction_service import ExtractionService
from app.services.workspace_service import WorkspaceService


@dataclass(frozen=True)
class UploadItem:
    filename: str
    media_type: str
    content: bytes


@dataclass(frozen=True)
class UploadedSource:
    artifact_id: str
    relative_path: str
    duplicate: bool = False


@dataclass(frozen=True)
class IngestionResult:
    sources: list[UploadedSource] = field(default_factory=list)


class IngestionService:
    def __init__(
        self,
        *,
        extraction_service: ExtractionService | None = None,
        max_upload_bytes: int = 20 * 1024 * 1024,
        max_parsed_chars: int = 1_000_000,
        max_total_workspace_bytes: int = 1024 * 1024 * 1024,
    ) -> None:
        self.extraction_service = extraction_service or ExtractionService()
        self.max_upload_bytes = max_upload_bytes
        self.max_parsed_chars = max_parsed_chars
        self.max_total_workspace_bytes = max_total_workspace_bytes

    def ingest_uploads(
        self,
        session: Session,
        workspace: WorkspaceService,
        artifact_service: ArtifactService,
        artifact_repository: ArtifactRepository,
        uploads: list[UploadItem],
    ) -> IngestionResult:
        sources: list[UploadedSource] = []
        for upload in uploads:
            self._validate_size(workspace, upload)
            content_hash = hashlib.sha256(upload.content).hexdigest()
            existing = artifact_repository.get_source_by_content_hash(session, content_hash)
            if existing is not None:
                sources.append(
                    UploadedSource(
                        artifact_id=existing.id,
                        relative_path=existing.relative_path,
                        duplicate=True,
                    )
                )
                continue

            source = artifact_service.store_source(
                source_filename=upload.filename,
                media_type=upload.media_type,
                content=upload.content,
            )
            extracted = self.extraction_service.extract(upload.filename, upload.media_type, upload.content)
            source_ref = f"source:{source.artifact_id}"
            if extracted is not None and extracted.should_write_extracted_artifact:
                artifact_service.create_markdown(
                    f"sources/extracted/{source.artifact_id}.md",
                    kind="extracted",
                    body=self._truncate(extracted.text),
                    source_refs=[source_ref],
                    origin="observed",
                )
            if extracted is not None:
                self._organize_material(
                    workspace,
                    artifact_service,
                    upload.filename,
                    self._truncate(extracted.text),
                    source_ref,
            )
            workspace.rebuild_projection(session, artifact_repository, artifact_service)
            session.add(
                models.ProcessingJob(
                    operation="ingest",
                    artifact_id=source.artifact_id,
                    status="completed",
                    attempts=1,
                    idempotency_key=f"ingest:{source.artifact_id}:{source.content_hash}",
                    started_at=datetime.now(UTC),
                    completed_at=datetime.now(UTC),
                )
            )
            session.flush()
            sources.append(
                UploadedSource(
                    artifact_id=source.artifact_id,
                    relative_path=source.relative_path,
                    duplicate=False,
                )
            )
        return IngestionResult(sources=sources)

    def _organize_material(
        self,
        workspace: WorkspaceService,
        artifact_service: ArtifactService,
        filename: str,
        text: str,
        source_ref: str,
    ) -> None:
        route = self._route(filename, text)
        if route == "candidate":
            self._create_or_update(
                workspace,
                artifact_service,
                "profile/candidate.md",
                "candidate_profile",
                "# 候选人画像\n\n## 来源资料摘要\n\n" + self._summary(text),
                "来源资料摘要",
                self._summary(text),
                source_ref,
            )
            return
        if route == "target":
            self._create_or_update(
                workspace,
                artifact_service,
                "profile/target.md",
                "target_profile",
                "# 目标岗位\n\n## 来源资料摘要\n\n" + self._summary(text),
                "来源资料摘要",
                self._summary(text),
                source_ref,
            )
            return
        slug = self._slug(Path(filename).stem)
        title = Path(filename).stem.replace("-", " ").strip() or "知识点"
        artifact_service.create_markdown(
            f"knowledge/{slug}.md",
            kind="knowledge",
            body=f"# {title}\n\n## 用户原始理解\n\n{self._summary(text)}\n",
            source_refs=[source_ref],
            origin="human",
        )

    def _create_or_update(
        self,
        workspace: WorkspaceService,
        artifact_service: ArtifactService,
        relative_path: str,
        kind: str,
        initial_body: str,
        section: str,
        section_body: str,
        source_ref: str,
    ) -> None:
        path = workspace.resolve_path(relative_path)
        if not path.exists():
            artifact_service.create_markdown(
                relative_path,
                kind=kind,  # type: ignore[arg-type]
                body=initial_body,
                source_refs=[source_ref],
                origin="human",
            )
            return
        current = artifact_service.read_markdown(relative_path)
        artifact_service.update_sections(
            relative_path,
            expected_revision=current.front_matter.revision,
            sections={section: section_body},
            edited_by="system",
        )

    def _route(self, filename: str, text: str) -> str:
        haystack = f"{filename}\n{text}".lower()
        if any(token in haystack for token in ("resume", "cv", "简历")):
            return "candidate"
        if any(token in haystack for token in ("jd", "job description", "岗位", "职位")):
            return "target"
        return "knowledge"

    def _summary(self, text: str) -> str:
        stripped = re.sub(r"\s+", " ", text).strip()
        return stripped[:1200] if stripped else "（空内容）"

    def _slug(self, value: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
        return slug or "knowledge"

    def _truncate(self, text: str) -> str:
        return text[: self.max_parsed_chars]

    def _validate_size(self, workspace: WorkspaceService, upload: UploadItem) -> None:
        if len(upload.content) > self.max_upload_bytes:
            raise ValueError("Uploaded files must be 20 MiB or smaller.")
        current_size = sum(path.stat().st_size for path in workspace.root.rglob("*") if path.is_file())
        if current_size + len(upload.content) > self.max_total_workspace_bytes:
            raise ValueError("Workspace storage limit exceeded.")
