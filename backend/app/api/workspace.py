from __future__ import annotations

from collections.abc import Iterator

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.sse import http_error_payload, sse_event
from app.core.artifact_permissions import (
    ALLOWED_OPERATIONS,
    ArtifactPermissionError,
    assert_operation_allowed,
)
from app.core.errors import bad_request
from app.core.errors import conflict as conflict_error
from app.core.errors import not_found
from app.core.errors import service_unavailable
from app.db.session import session_scope
from app.repositories.artifact_repository import ArtifactRepository
from app.repositories.vector_store import VectorStoreError
from app.repositories.workspace_settings_repository import WorkspaceSettingsRepository
from app.services.artifact_service import ArtifactConflict
from app.services.ingestion_service import IngestionService, UploadItem
from app.services.index_service import IndexService
from app.services.markdown_utils import (
    markdown_list_items,
    markdown_sections,
)
from app.services.model_service import LearningNoteSummaryResult, ModelService
from app.services.workspace_content_service import (
    LearningNotePersistenceResult,
    RealInterviewRecordPersistenceResult,
    WorkspaceContentProjectionError,
    WorkspaceContentService,
)


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
    card_markdown: str


class RealInterviewRecordRequest(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    language: str = "zh-CN"


class RealInterviewRecordResponse(BaseModel):
    raw_artifact: "ArtifactSummaryResponse"
    high_frequency_artifact: "ArtifactSummaryResponse"
    status_artifact: "ArtifactSummaryResponse"
    questions: list[str]
    weak_points: list[str]


class ArtifactSummaryResponse(BaseModel):
    id: str
    kind: str
    owner: str
    relative_path: str
    display_name: str
    revision: int
    processing_status: str
    index_status: str
    recovery_required: bool
    allowed_operations: list[str]
    created_at: datetime
    updated_at: datetime


class ArtifactListResponse(BaseModel):
    artifacts: list[ArtifactSummaryResponse]


class PreparationTaskResponse(BaseModel):
    title: str
    reason: str
    source_artifact_id: str | None = None
    source_relative_path: str | None = None


class PreparationTasksResponse(BaseModel):
    tasks: list[PreparationTaskResponse]


class ArtifactDetailResponse(ArtifactSummaryResponse):
    body: str | None = None


class ArtifactDeleteResponse(BaseModel):
    id: str
    status: str


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


@router.get("/preparation-tasks", response_model=PreparationTasksResponse)
def preparation_tasks(
    request: Request,
    session: Session = Depends(get_session),
) -> PreparationTasksResponse:
    repository = ArtifactRepository()
    status = repository.get_by_relative_path(session, "review/status.md")
    if status is None:
        return PreparationTasksResponse(tasks=[])
    try:
        body = request.app.state.artifact_service.read_markdown(status.relative_path).body
    except Exception:
        return PreparationTasksResponse(tasks=[])
    sections = markdown_sections(body)
    task_items = (
        markdown_list_items(sections.get("当前重点") or "")
        or markdown_list_items(sections.get("最近练习") or "")
        or markdown_list_items(sections.get("最近整理") or "")
    )
    return PreparationTasksResponse(
        tasks=[
            PreparationTaskResponse(
                title=task,
                reason="来自复习状态",
                source_artifact_id=status.id,
                source_relative_path=status.relative_path,
            )
            for task in task_items[:3]
        ]
    )


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


@router.delete("/artifacts/{artifact_id}", response_model=ArtifactDeleteResponse)
def delete_artifact(
    artifact_id: str,
    request: Request,
    session: Session = Depends(get_session),
) -> ArtifactDeleteResponse:
    repository = ArtifactRepository()
    artifact = repository.get(session, artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    workspace_settings = WorkspaceSettingsRepository().get_or_create(session)
    index_service = IndexService()
    target_collection = workspace_settings.active_collection or index_service.settings.qdrant_collection
    try:
        index_service.vector_store.delete_artifact_chunks(target_collection, artifact_id)
    except VectorStoreError as exc:
        raise service_unavailable(
            "vector_delete_failed",
            "Artifact chunks could not be removed from the vector index.",
        ) from exc
    try:
        request.app.state.artifact_service.delete_artifact_files(
            artifact.relative_path,
            artifact_id=artifact.id,
            remove_source_sidecar=artifact.kind == "source",
        )
    except OSError as exc:
        raise bad_request("artifact_delete_failed", "Artifact could not be deleted.") from exc
    request.app.state.workspace_service.rebuild_projection(
        session,
        repository,
        request.app.state.artifact_service,
    )
    return ArtifactDeleteResponse(id=artifact_id, status="deleted")


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
                yield sse_event("delta", {"text": chunk})
            assistant_message = "".join(chunks).strip()
            summary = WorkspaceContentService.parse_learning_note_summary(
                assistant_message,
                note,
                payload.language,
            )
            service = _workspace_content_service(request)
            response = _learning_note_response(
                service.persist_learning_note(note, payload.language, summary)
            )
            _enqueue_index_rebuild(request, background_tasks)
            yield sse_event("result", response.model_dump(mode="json"))
        except WorkspaceContentProjectionError as error:
            yield sse_event(
                "error",
                {
                    "code": error.code,
                    "message": error.message,
                    "status_code": 500,
                },
            )
        except HTTPException as error:
            yield sse_event("error", http_error_payload(error))
        except Exception:
            yield sse_event(
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


@router.post("/real-interview-records", response_model=RealInterviewRecordResponse)
def record_real_interview(
    payload: RealInterviewRecordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> RealInterviewRecordResponse:
    record = payload.text.strip()
    if not record:
        raise bad_request("real_interview_record_empty", "Real interview record text is required.")
    try:
        service = _workspace_content_service(request)
        response = _real_interview_record_response(
            service.persist_real_interview_record(record, payload.language)
        )
    except WorkspaceContentProjectionError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    _enqueue_index_rebuild(request, background_tasks)
    return response


def _workspace_content_service(request: Request) -> WorkspaceContentService:
    return WorkspaceContentService(
        workspace_service=request.app.state.workspace_service,
        artifact_service=request.app.state.artifact_service,
        session_factory=request.app.state.session_factory,
    )


def _enqueue_index_rebuild(request: Request, background_tasks: BackgroundTasks) -> None:
    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        request.app.state.workspace_service,
        ArtifactRepository(),
    )


def _learning_note_response(result: LearningNotePersistenceResult) -> LearningNoteResponse:
    return LearningNoteResponse(
        source=UploadedSourceResponse(
            artifact_id=result.source.artifact_id,
            relative_path=result.source.relative_path,
            duplicate=result.source.duplicate,
        ),
        artifact=_summary(result.artifact),
        summary=result.summary,
        card_markdown=result.card_markdown,
    )


def _real_interview_record_response(
    result: RealInterviewRecordPersistenceResult,
) -> RealInterviewRecordResponse:
    return RealInterviewRecordResponse(
        raw_artifact=_summary(result.raw_artifact),
        high_frequency_artifact=_summary(result.high_frequency_artifact),
        status_artifact=_summary(result.status_artifact),
        questions=result.questions,
        weak_points=result.weak_points,
    )


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
        owner=_owner(artifact),
        relative_path=artifact.relative_path,
        display_name=_display_name(artifact),
        revision=artifact.revision,
        processing_status=artifact.processing_status,
        index_status=artifact.index_status,
        recovery_required=artifact.recovery_required,
        allowed_operations=sorted(ALLOWED_OPERATIONS.get(artifact.kind, set())),
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


def _display_name(artifact) -> str:
    if artifact.kind == "source" and artifact.source_filename:
        return artifact.source_filename
    return Path(artifact.relative_path).name


def _owner(artifact) -> str:
    if artifact.kind in {"candidate_profile", "target_profile"}:
        return "profile"
    if artifact.kind in {"mastery", "plan"}:
        return "state"
    parts = Path(artifact.relative_path).parts
    return parts[0] if parts else artifact.kind
