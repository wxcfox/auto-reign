from __future__ import annotations

from collections.abc import Iterator

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.artifact_permissions import (
    ALLOWED_OPERATIONS,
    ArtifactPermissionError,
    assert_operation_allowed,
    validate_plan_task_count,
)
from app.core.errors import bad_request
from app.core.errors import conflict as conflict_error
from app.core.errors import not_found
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactConflict
from app.services.ingestion_service import IngestionService, UploadItem
from app.services.index_service import IndexService
from app.services.model_service import LearningNoteSummaryResult, ModelService


router = APIRouter(prefix="/api/workspace")


class WorkspaceStatusResponse(BaseModel):
    schema_version: int
    language: str
    artifact_count: int
    initialized: bool = True


class UploadedSourceResponse(BaseModel):
    artifact_id: str
    relative_path: str
    duplicate: bool


class UploadMaterialsResponse(BaseModel):
    sources: list[UploadedSourceResponse]


class LearningNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    language: str = "zh-CN"
    provider: str | None = None
    model: str | None = None


class LearningNoteResponse(BaseModel):
    source: UploadedSourceResponse
    artifact: "ArtifactSummaryResponse"
    summary: LearningNoteSummaryResult


class ArtifactSummaryResponse(BaseModel):
    id: str
    kind: str
    relative_path: str
    display_name: str
    revision: int
    processing_status: str
    index_status: str
    recovery_required: bool
    allowed_operations: list[str]


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummaryResponse]


class ArtifactDetailResponse(ArtifactSummaryResponse):
    body: str | None = None


class ReplaceBodyRequest(BaseModel):
    expected_revision: int
    body: str


def get_session(request: Request) -> Iterator[Session]:
    with session_scope(request.app.state.session_factory) as session:
        yield session


@router.get("", response_model=WorkspaceStatusResponse)
def workspace_status(session: Session = Depends(get_session)) -> WorkspaceStatusResponse:
    settings = WorkspaceSettingsRepository().get_or_create(session)
    artifacts = ArtifactRepository().list(session)
    return WorkspaceStatusResponse(
        schema_version=settings.schema_version,
        language=settings.language,
        artifact_count=len(artifacts),
    )


@router.get("/artifacts", response_model=ArtifactListResponse)
def list_artifacts(session: Session = Depends(get_session)) -> ArtifactListResponse:
    artifacts = ArtifactRepository().list(session)
    return ArtifactListResponse(artifacts=[_summary(artifact) for artifact in artifacts])


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDetailResponse)
def get_artifact(
    artifact_id: str, request: Request, session: Session = Depends(get_session)
) -> ArtifactDetailResponse:
    artifact = ArtifactRepository().get(session, artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    body: str | None = None
    if artifact.kind not in {"source"}:
        try:
            body = request.app.state.artifact_service.read_markdown(artifact.relative_path).body
        except Exception:
            body = None
    return ArtifactDetailResponse(**_summary(artifact).model_dump(), body=body)


@router.put("/artifacts/{artifact_id}/body", response_model=ArtifactSummaryResponse)
def replace_artifact_body(
    artifact_id: str,
    payload: ReplaceBodyRequest,
    request: Request,
    session: Session = Depends(get_session),
) -> ArtifactSummaryResponse:
    repository = ArtifactRepository()
    artifact = repository.get(session, artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    try:
        assert_operation_allowed(artifact.kind, "replace_body")
        if artifact.kind == "plan":
            validate_plan_task_count(_extract_plan_tasks(payload.body))
    except ArtifactPermissionError as exc:
        if artifact.kind in {"source", "extracted", "practice", "mastery"}:
            raise HTTPException(status_code=403, detail={"code": "artifact_read_only", "message": str(exc)}) from exc
        raise bad_request("artifact_edit_invalid", str(exc)) from exc
    try:
        request.app.state.artifact_service.replace_body(
            artifact.relative_path,
            expected_revision=payload.expected_revision,
            body=payload.body,
            edited_by="user",
        )
    except ArtifactConflict as exc:
        raise conflict_error("artifact_revision_conflict", str(exc)) from exc
    request.app.state.workspace_service.rebuild_projection(
        session,
        repository,
        request.app.state.artifact_service,
    )
    updated = repository.get(session, artifact_id)
    return _summary(updated)


@router.post("/rebuild-projection", response_model=WorkspaceStatusResponse)
def rebuild_projection(request: Request, session: Session = Depends(get_session)) -> WorkspaceStatusResponse:
    request.app.state.workspace_service.rebuild_projection(
        session,
        ArtifactRepository(),
        request.app.state.artifact_service,
    )
    settings = WorkspaceSettingsRepository().get_or_create(session)
    artifacts = ArtifactRepository().list(session)
    return WorkspaceStatusResponse(
        schema_version=settings.schema_version,
        language=settings.language,
        artifact_count=len(artifacts),
    )


@router.post("/materials/upload", response_model=UploadMaterialsResponse)
async def upload_materials(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
) -> UploadMaterialsResponse:
    uploads = [
        UploadItem(
            filename=file.filename or "material.txt",
            media_type=file.content_type or "application/octet-stream",
            content=await file.read(),
        )
        for file in files
    ]
    try:
        with session_scope(request.app.state.session_factory) as session:
            result = IngestionService().ingest_uploads(
                session,
                request.app.state.workspace_service,
                request.app.state.artifact_service,
                ArtifactRepository(),
                uploads,
            )
    except ValueError as exc:
        raise bad_request("material_upload_invalid", str(exc)) from exc
    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return UploadMaterialsResponse(
        sources=[UploadedSourceResponse(**source.__dict__) for source in result.sources]
    )


@router.post("/learning-notes", response_model=LearningNoteResponse)
def record_learning_note(
    payload: LearningNoteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> LearningNoteResponse:
    note = payload.text.strip()
    if not note:
        raise bad_request("learning_note_empty", "Learning note text is required.")

    summary = ModelService().summarize_learning_note(
        note,
        language=payload.language,
        provider=payload.provider,
        model=payload.model,
    )
    return _persist_learning_note(note, payload.language, summary, request, background_tasks)


@router.post("/learning-notes/stream")
def record_learning_note_stream(
    payload: LearningNoteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> StreamingResponse:
    note = payload.text.strip()
    if not note:
        raise bad_request("learning_note_empty", "Learning note text is required.")

    def body() -> Iterator[str]:
        chunks: list[str] = []
        try:
            for chunk in ModelService().stream_learning_note_summary(
                note,
                language=payload.language,
                provider=payload.provider,
                model=payload.model,
            ):
                chunks.append(chunk)
                yield _sse_event("delta", {"text": chunk})
            assistant_message = "".join(chunks).strip()
            summary = _parse_learning_note_summary(assistant_message, note, payload.language)
            response = _persist_learning_note(
                note,
                payload.language,
                summary,
                request,
                background_tasks,
            )
            yield _sse_event("result", response.model_dump(mode="json"))
        except HTTPException as error:
            yield _sse_event("error", _http_error_payload(error))
        except Exception:
            yield _sse_event(
                "error",
                {
                    "code": "stream_failed",
                    "message": "The streaming response failed.",
                    "status_code": 502,
                },
            )

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _persist_learning_note(
    note: str,
    language: str,
    summary: LearningNoteSummaryResult,
    request: Request,
    background_tasks: BackgroundTasks,
) -> LearningNoteResponse:
    timestamp = datetime.now(UTC)
    source = request.app.state.artifact_service.store_source(
        source_filename=f"learning-note-{timestamp.strftime('%Y%m%d-%H%M%S')}.md",
        media_type="text/markdown",
        content=note.encode("utf-8"),
        language=language,
        uploaded_at=timestamp,
    )
    source_ref = f"source:{source.artifact_id}"
    knowledge_path = f"knowledge/{_slug(summary.title)}-{source.artifact_id[:8]}.md"
    request.app.state.artifact_service.create_markdown(
        knowledge_path,
        kind="knowledge",
        language=language,
        body=_learning_note_body(note, summary),
        source_refs=[source_ref],
        origin="llm",
        edited_by="system",
        now=timestamp,
    )

    with session_scope(request.app.state.session_factory) as session:
        repository = ArtifactRepository()
        request.app.state.workspace_service.rebuild_projection(
            session,
            repository,
            request.app.state.artifact_service,
        )
        source_artifact = repository.get(session, source.artifact_id)
        knowledge_artifact = repository.get_by_relative_path(session, knowledge_path)
        if source_artifact is None or knowledge_artifact is None:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "learning_note_projection_failed",
                    "message": "Learning note was saved but projection failed.",
                },
            )
        response = LearningNoteResponse(
            source=UploadedSourceResponse(
                artifact_id=source_artifact.id,
                relative_path=source_artifact.relative_path,
                duplicate=False,
            ),
            artifact=_summary(knowledge_artifact),
            summary=summary,
        )

    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return response


@router.post("/rebuild-index")
def rebuild_index(request: Request) -> dict[str, str]:
    collection = IndexService().rebuild_index(
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )
    return {"status": "ok", "collection": collection}


def _summary(artifact) -> ArtifactSummaryResponse:
    return ArtifactSummaryResponse(
        id=artifact.id,
        kind=artifact.kind,
        relative_path=artifact.relative_path,
        display_name=_display_name(artifact),
        revision=artifact.revision,
        processing_status=artifact.processing_status,
        index_status=artifact.index_status,
        recovery_required=artifact.recovery_required,
        allowed_operations=sorted(ALLOWED_OPERATIONS.get(artifact.kind, set())),
    )


def _display_name(artifact) -> str:
    if artifact.kind == "source" and artifact.source_filename:
        return artifact.source_filename
    return Path(artifact.relative_path).name


def _extract_plan_tasks(body: str) -> list[str]:
    tasks: list[str] = []
    for line in body.splitlines():
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", line):
            tasks.append(line.strip())
    return tasks


def _learning_note_body(note: str, summary: LearningNoteSummaryResult) -> str:
    sections = [
        f"# {summary.title}",
        "## 用户原始学习记录\n\n" + note,
        "## AI 整理摘要\n\n" + summary.summary,
    ]
    if summary.key_points:
        sections.append("## 关键点\n\n" + "\n".join(f"- {point}" for point in summary.key_points))
    if summary.interview_takeaways:
        sections.append(
            "## 面试表达\n\n"
            + "\n".join(f"- {takeaway}" for takeaway in summary.interview_takeaways)
        )
    if summary.follow_up_questions:
        sections.append(
            "## 可追问问题\n\n"
            + "\n".join(f"- {question}" for question in summary.follow_up_questions)
        )
    return "\n\n".join(sections) + "\n"


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "-", value).strip("-").lower()
    return slug[:80] or "learning-note"


def _sse_event(event: str, data: dict[str, object]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def _http_error_payload(error: HTTPException) -> dict[str, object]:
    if isinstance(error.detail, dict):
        return {
            "code": error.detail.get("code", "request_failed"),
            "message": error.detail.get("message", "Request failed."),
            "status_code": error.status_code,
        }
    return {
        "code": "request_failed",
        "message": str(error.detail),
        "status_code": error.status_code,
    }


def _parse_learning_note_summary(
    markdown: str,
    note: str,
    language: str,
) -> LearningNoteSummaryResult:
    title_match = re.search(r"^#\s+(.+)$", markdown, flags=re.MULTILINE)
    fallback_title = "学习记录" if language == "zh-CN" else "Learning note"
    title = title_match.group(1).strip()[:80] if title_match else _slug(note).replace("-", " ")
    sections = _markdown_sections(markdown)
    summary = (
        sections.get("summary")
        or sections.get("摘要")
        or sections.get("ai 整理摘要")
        or note[:240]
        or fallback_title
    )
    key_points = _markdown_list_items(
        sections.get("key points") or sections.get("关键点") or ""
    )
    interview_takeaways = _markdown_list_items(
        sections.get("interview expression") or sections.get("面试表达") or ""
    )
    follow_up_questions = _markdown_list_items(
        sections.get("follow-up questions") or sections.get("可追问问题") or ""
    )
    return LearningNoteSummaryResult(
        title=title or fallback_title,
        summary=summary.strip(),
        key_points=key_points,
        interview_takeaways=interview_takeaways,
        follow_up_questions=follow_up_questions,
    )


def _markdown_sections(markdown: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+)$", line.strip())
        if heading:
            current = heading.group(1).strip().lower()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _markdown_list_items(value: str) -> list[str]:
    items: list[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^(?:[-*]|\d+[.)])\s+", "", stripped).strip()
        if stripped:
            items.append(stripped)
    return items
