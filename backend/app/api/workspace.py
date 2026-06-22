from __future__ import annotations

from collections.abc import Iterator

import re

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
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


class ArtifactSummaryResponse(BaseModel):
    id: str
    kind: str
    relative_path: str
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
        revision=artifact.revision,
        processing_status=artifact.processing_status,
        index_status=artifact.index_status,
        recovery_required=artifact.recovery_required,
        allowed_operations=sorted(ALLOWED_OPERATIONS.get(artifact.kind, set())),
    )


def _extract_plan_tasks(body: str) -> list[str]:
    tasks: list[str] = []
    for line in body.splitlines():
        if re.match(r"^\s*(?:[-*]|\d+[.)])\s+\S", line):
            tasks.append(line.strip())
    return tasks
