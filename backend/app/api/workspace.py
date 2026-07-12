from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_session, get_user_scope
from app.api.sse import http_error_payload, sse_event
from app.core.artifact_permissions import (
    ALLOWED_OPERATIONS,
    ArtifactPermissionError,
    assert_operation_allowed,
)
from app.core.user_scope import UserScope
from app.core.errors import bad_request
from app.core.errors import conflict as conflict_error
from app.core.errors import not_found
from app.core.errors import service_unavailable
from app.db.session import session_scope
from app.schemas.workspace import (
    ArtifactDeleteResponse,
    ArtifactDetailResponse,
    ArtifactListResponse,
    ArtifactSummaryResponse,
    LearningNoteRequest,
    LearningNoteResponse,
    RealInterviewRecordRequest,
    RealInterviewRecordResponse,
    ReplaceBodyRequest,
    UploadedSourceResponse,
    UploadMaterialsResponse,
    WorkspaceDirectoryResponse,
    WorkspaceFileContentResponse,
    WorkspaceFileResponse,
    WorkspaceFilesResponse,
)
from app.services.artifact_metadata import (
    artifact_index_status,
    artifact_processing_status,
    artifact_recovery_required,
    artifact_source_filename,
)
from app.services.artifact_service import ArtifactService
from app.services.index_service import IndexService
from app.services.model_service import ModelService
from app.services.workspace_service import UnsafeWorkspacePath, WorkspaceService
from app.services.workspace_paths import WORKSPACE_DIRECTORIES


router = APIRouter(prefix="/api/workspace")
MAX_WORKSPACE_FILE_PREVIEW_BYTES = 1_000_000


@router.get("/artifacts", response_model=ArtifactListResponse)
def list_artifacts(
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ArtifactListResponse:
    from app.repositories.artifact_repository import ArtifactRepository

    _workspace_services(scope)
    artifacts = ArtifactRepository().list(session, user_id=scope.user_id)
    return ArtifactListResponse(artifacts=[_summary(artifact) for artifact in artifacts])


@router.get("/files", response_model=WorkspaceFilesResponse)
def list_files(
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> WorkspaceFilesResponse:
    from app.repositories.artifact_repository import ArtifactRepository

    workspace, _ = _workspace_services(scope)
    artifacts_by_path = {
        artifact.relative_path: artifact
        for artifact in ArtifactRepository().list(session, user_id=scope.user_id)
    }
    return WorkspaceFilesResponse(
        root="workspace",
        directories=_workspace_directories(workspace.root, artifacts_by_path),
    )


@router.get("/files/content", response_model=WorkspaceFileContentResponse)
def get_file_content(
    relative_path: str,
    scope: UserScope = Depends(get_user_scope),
) -> WorkspaceFileContentResponse:
    workspace, _ = _workspace_services(scope)
    try:
        path = workspace.resolve_path(relative_path)
    except UnsafeWorkspacePath as exc:
        raise bad_request("workspace_file_path_invalid", "Workspace file path is invalid.") from exc
    if not path.exists() or not path.is_file():
        raise not_found("workspace_file_not_found", "Workspace file not found.")
    try:
        stat = path.stat()
    except OSError as exc:
        raise bad_request("workspace_file_unreadable", "Workspace file could not be read.") from exc
    if stat.st_size > MAX_WORKSPACE_FILE_PREVIEW_BYTES:
        raise bad_request("workspace_file_too_large", "Workspace file is too large to preview.")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise bad_request("workspace_file_not_text", "Workspace file is not UTF-8 text.") from exc
    except OSError as exc:
        raise bad_request("workspace_file_unreadable", "Workspace file could not be read.") from exc
    return WorkspaceFileContentResponse(
        name=path.name,
        relative_path=workspace.to_relative_path(path),
        size_bytes=stat.st_size,
        updated_at=datetime.fromtimestamp(stat.st_mtime, UTC),
        content=content,
    )


@router.get("/artifacts/{artifact_id}", response_model=ArtifactDetailResponse)
def get_artifact(
    artifact_id: str,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ArtifactDetailResponse:
    from app.repositories.artifact_repository import ArtifactRepository

    _, artifact_service = _workspace_services(scope)
    artifact = ArtifactRepository().get(
        session,
        user_id=scope.user_id,
        artifact_id=artifact_id,
    )
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    body: str | None = None
    if artifact.kind not in {"source"}:
        try:
            body = artifact_service.read_markdown(artifact.relative_path).body
        except Exception:
            body = None
    return ArtifactDetailResponse(**_summary(artifact).model_dump(), body=body)


@router.put("/artifacts/{artifact_id}/body", response_model=ArtifactSummaryResponse)
def replace_artifact_body(
    artifact_id: str,
    payload: ReplaceBodyRequest,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ArtifactSummaryResponse:
    from app.repositories.artifact_repository import ArtifactRepository
    from app.services.artifact_service import ArtifactConflict

    workspace, artifact_service = _workspace_services(scope)
    repository = ArtifactRepository()
    artifact = repository.get(session, user_id=scope.user_id, artifact_id=artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    try:
        assert_operation_allowed(artifact.kind, "replace_body")
    except ArtifactPermissionError as exc:
        if artifact.kind in {"source", "extracted", "practice", "mastery"}:
            raise HTTPException(status_code=403, detail={"code": "artifact_read_only", "message": str(exc)}) from exc
        raise bad_request("artifact_edit_invalid", str(exc)) from exc
    try:
        artifact_service.replace_body(
            artifact.relative_path,
            expected_revision=payload.expected_revision,
            body=payload.body,
            edited_by="user",
        )
    except ArtifactConflict as exc:
        raise conflict_error("artifact_revision_conflict", str(exc)) from exc
    workspace.rebuild_projection(
        session,
        repository,
        artifact_service,
        user_id=scope.user_id,
    )
    updated = repository.get(session, user_id=scope.user_id, artifact_id=artifact_id)
    return _summary(updated)


@router.delete("/artifacts/{artifact_id}", response_model=ArtifactDeleteResponse)
def delete_artifact(
    artifact_id: str,
    session: Session = Depends(get_session),
    scope: UserScope = Depends(get_user_scope),
) -> ArtifactDeleteResponse:
    from app.repositories.artifact_repository import ArtifactRepository
    from app.repositories.vector_store import VectorStoreError

    workspace, artifact_service = _workspace_services(scope)
    repository = ArtifactRepository()
    artifact = repository.get(session, user_id=scope.user_id, artifact_id=artifact_id)
    if artifact is None:
        raise not_found("artifact_not_found", "Artifact not found.")
    index_service = IndexService()
    target_collection = _active_collection(session, scope)
    try:
        index_service.vector_store.delete_artifact_chunks(target_collection, artifact_id)
    except VectorStoreError as exc:
        raise service_unavailable(
            "vector_delete_failed",
            "Artifact chunks could not be removed from the vector index.",
        ) from exc
    try:
        artifact_service.delete_artifact_files(
            artifact.relative_path,
            artifact_id=artifact.id,
            remove_source_sidecar=artifact.kind == "source",
        )
    except OSError as exc:
        raise bad_request("artifact_delete_failed", "Artifact could not be deleted.") from exc
    workspace.rebuild_projection(
        session,
        repository,
        artifact_service,
        user_id=scope.user_id,
    )
    return ArtifactDeleteResponse(id=artifact_id, status="deleted")


@router.post("/materials/upload", response_model=UploadMaterialsResponse)
async def upload_materials(
    request: Request,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    scope: UserScope = Depends(get_user_scope),
) -> UploadMaterialsResponse:
    from app.repositories.artifact_repository import ArtifactRepository
    from app.services.ingestion_service import IngestionService, UploadItem

    workspace, artifact_service = _workspace_services(scope)
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
                scope.user_id,
                workspace,
                artifact_service,
                ArtifactRepository(),
                uploads,
            )
    except ValueError as exc:
        raise bad_request("material_upload_invalid", str(exc)) from exc
    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        workspace,
        ArtifactRepository(),
        user_id=scope.user_id,
        qdrant_prefix=scope.qdrant_prefix,
    )
    return UploadMaterialsResponse(
        sources=[UploadedSourceResponse(**source.__dict__) for source in result.sources]
    )


@router.post("/learning-notes/stream")
def record_learning_note_stream(
    payload: LearningNoteRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    scope: UserScope = Depends(get_user_scope),
) -> StreamingResponse:
    from app.services.workspace_content_service import (
        WorkspaceContentProjectionError,
    )

    note = payload.text.strip()
    if not note:
        raise bad_request("learning_note_empty", "Learning note text is required.")
    if payload.conversation_id:
        _require_learning_conversation(request, scope, payload.conversation_id)

    def body() -> Iterator[str]:
        try:
            summary = ModelService().generate_learning_note_summary(
                note,
                language=payload.language,
                provider=payload.provider,
                model=payload.model,
            )
            service = _workspace_content_service(request, scope)
            response = _learning_note_response(
                service.persist_learning_note(note, payload.language, summary)
            )
            yield sse_event("delta", {"text": _learning_assistant_message(response)})
            response.conversation_id = _persist_learning_conversation(
                request,
                scope,
                payload,
                note,
                response,
            )
            _enqueue_index_rebuild(request, background_tasks, scope)
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
    scope: UserScope = Depends(get_user_scope),
) -> RealInterviewRecordResponse:
    from app.services.workspace_content_service import WorkspaceContentProjectionError

    record = payload.text.strip()
    if not record:
        raise bad_request("real_interview_record_empty", "Real interview record text is required.")
    try:
        service = _workspace_content_service(request, scope)
        response = _real_interview_record_response(
            service.persist_real_interview_record(record, payload.language)
        )
    except WorkspaceContentProjectionError as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    _enqueue_index_rebuild(request, background_tasks, scope)
    return response


def _workspace_content_service(request: Request, scope: UserScope) -> Any:
    from app.services.workspace_content_service import WorkspaceContentService

    workspace, artifact_service = _workspace_services(scope)
    return WorkspaceContentService(
        user_id=scope.user_id,
        workspace_service=workspace,
        artifact_service=artifact_service,
        session_factory=request.app.state.session_factory,
    )


def _enqueue_index_rebuild(
    request: Request,
    background_tasks: BackgroundTasks,
    scope: UserScope,
) -> None:
    from app.repositories.artifact_repository import ArtifactRepository

    workspace, _ = _workspace_services(scope)
    background_tasks.add_task(
        IndexService().rebuild_index,
        request.app.state.session_factory,
        workspace,
        ArtifactRepository(),
        user_id=scope.user_id,
        qdrant_prefix=scope.qdrant_prefix,
    )


def _persist_learning_conversation(
    request: Request,
    scope: UserScope,
    payload: LearningNoteRequest,
    note: str,
    response: LearningNoteResponse,
) -> str:
    from app.db import models
    from app.repositories.conversation_repository import ConversationRepository

    with session_scope(request.app.state.session_factory) as session:
        repository = ConversationRepository()
        conversation = None
        if payload.conversation_id:
            conversation = repository.get(
                session,
                user_id=scope.user_id,
                conversation_id=payload.conversation_id,
                kind="learning",
            )
            if conversation is None:
                raise not_found("learning_session_not_found", "Learning conversation not found.")
        if conversation is None:
            conversation = repository.create(
                session,
                user_id=scope.user_id,
                kind="learning",
                title=(response.summary.title.strip() or "学习记录")[:255],
                status="active",
                config_json={
                    "language": payload.language,
                    "provider": payload.provider or "",
                    "model": payload.model or "",
                },
                summary_json={},
            )

        assistant_markdown = _learning_assistant_message(response)
        repository.add_message(
            session,
            user_id=scope.user_id,
            conversation_id=conversation.id,
            role="user",
            message_type="learning_input",
            content=note,
            metadata_json={
                "source_artifact_id": response.source.artifact_id,
                "source_relative_path": response.source.relative_path,
            },
        )
        repository.add_message(
            session,
            user_id=scope.user_id,
            conversation_id=conversation.id,
            role="assistant",
            message_type="learning_summary",
            content=assistant_markdown,
            metadata_json={
                "artifact_id": response.artifact.id,
                "artifact_path": response.artifact.relative_path,
                "summary": response.summary.model_dump(mode="json"),
            },
        )
        title = (response.summary.title.strip() or conversation.title)[:255]
        conversation.title = title
        conversation.summary_json = {
            **(conversation.summary_json or {}),
            "title": title,
            "last_message": assistant_markdown,
        }
        conversation.updated_at = models._now()
        session.flush()
        return conversation.id


def _require_learning_conversation(
    request: Request,
    scope: UserScope,
    conversation_id: str,
) -> None:
    from app.repositories.conversation_repository import ConversationRepository

    with session_scope(request.app.state.session_factory) as session:
        conversation = ConversationRepository().get(
            session,
            user_id=scope.user_id,
            conversation_id=conversation_id,
            kind="learning",
        )
        if conversation is None:
            raise not_found("learning_session_not_found", "Learning conversation not found.")


def _learning_assistant_message(response: LearningNoteResponse) -> str:
    title = response.summary.title.strip()
    if not title:
        return response.card_markdown
    return f"# {title}\n\n{response.card_markdown.strip()}"


def _learning_note_response(result: Any) -> LearningNoteResponse:
    return LearningNoteResponse(
        conversation_id="",
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
    result: Any,
) -> RealInterviewRecordResponse:
    return RealInterviewRecordResponse(
        raw_artifact=_summary(result.raw_artifact),
        high_frequency_artifact=_summary(result.high_frequency_artifact),
        status_artifact=_summary(result.status_artifact),
        questions=result.questions,
        weak_points=result.weak_points,
    )


@router.post("/rebuild-index")
def rebuild_index(
    request: Request,
    scope: UserScope = Depends(get_user_scope),
) -> dict[str, str]:
    from app.repositories.artifact_repository import ArtifactRepository

    workspace, _ = _workspace_services(scope)
    collection = IndexService().rebuild_index(
        request.app.state.session_factory,
        workspace,
        ArtifactRepository(),
        user_id=scope.user_id,
        qdrant_prefix=scope.qdrant_prefix,
    )
    return {"status": "ok", "collection": collection}


def _workspace_services(scope: UserScope) -> tuple[WorkspaceService, ArtifactService]:
    workspace = WorkspaceService(
        scope.workspace_root,
        default_manifest_path=scope.default_manifest_path,
    )
    workspace.initialize()
    return workspace, ArtifactService(workspace)


def _active_collection(session: Session, scope: UserScope) -> str:
    from app.db import models

    user = session.get(models.User, scope.user_id)
    if user is None:
        return scope.qdrant_prefix
    active_collection = (user.settings_json or {}).get("active_collection")
    return active_collection if isinstance(active_collection, str) and active_collection else scope.qdrant_prefix


def _workspace_directories(
    root: Path,
    artifacts_by_path: dict[str, Any],
) -> list[WorkspaceDirectoryResponse]:
    directory_paths = [root]
    for path in root.rglob("*"):
        try:
            if path.is_symlink() or not path.is_dir():
                continue
        except OSError:
            continue
        directory_paths.append(path)

    return [
        _workspace_directory_response(directory, root, artifacts_by_path)
        for directory in sorted(
            directory_paths,
            key=lambda directory: _directory_sort_key(_relative_directory(directory, root)),
        )
    ]


def _workspace_directory_response(
    directory: Path,
    root: Path,
    artifacts_by_path: dict[str, Any],
) -> WorkspaceDirectoryResponse:
    relative_path = _relative_directory(directory, root)
    files: list[WorkspaceFileResponse] = []
    child_directory_count = 0
    try:
        directory_stat = directory.stat()
        directory_updated_at = datetime.fromtimestamp(directory_stat.st_mtime, UTC)
    except OSError:
        directory_updated_at = datetime.now(UTC)
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError:
        entries = []
    for entry in entries:
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                child_directory_count += 1
                continue
            if not entry.is_file():
                continue
            stat = entry.stat()
        except OSError:
            continue
        file_relative_path = entry.relative_to(root).as_posix()
        artifact = artifacts_by_path.get(file_relative_path)
        file_updated_at = datetime.fromtimestamp(stat.st_mtime, UTC)
        files.append(
            WorkspaceFileResponse(
                name=entry.name,
                relative_path=file_relative_path,
                directory=relative_path,
                size_bytes=stat.st_size,
                created_at=artifact.created_at if artifact is not None else file_updated_at,
                updated_at=artifact.updated_at if artifact is not None else file_updated_at,
                owner=_owner(artifact) if artifact is not None else "workspace",
                kind=artifact.kind if artifact is not None else "file",
                processing_status=artifact_processing_status(artifact)
                if artifact is not None
                else "completed",
                index_status=artifact_index_status(artifact)
                if artifact is not None
                else "completed",
                recovery_required=artifact_recovery_required(artifact)
                if artifact is not None
                else False,
                allowed_operations=sorted(ALLOWED_OPERATIONS.get(artifact.kind, set()))
                if artifact is not None
                else [],
                artifact_id=artifact.id if artifact is not None else None,
                artifact_kind=artifact.kind if artifact is not None else None,
            )
        )
    return WorkspaceDirectoryResponse(
        name="workspace" if not relative_path else directory.name,
        relative_path=relative_path,
        depth=0 if not relative_path else len(Path(relative_path).parts),
        file_count=len(files),
        child_directory_count=child_directory_count,
        created_at=directory_updated_at,
        updated_at=directory_updated_at,
        files=files,
    )


def _relative_directory(directory: Path, root: Path) -> str:
    if directory == root:
        return ""
    return directory.relative_to(root).as_posix()


def _directory_sort_key(relative_path: str) -> tuple[int, tuple[str, ...]]:
    if not relative_path:
        return (-1, ())
    parts = Path(relative_path).parts
    top_level_order = {directory: index for index, directory in enumerate(WORKSPACE_DIRECTORIES)}
    first = parts[0]
    return (top_level_order.get(first, len(top_level_order)), parts)


def _summary(artifact) -> ArtifactSummaryResponse:
    return ArtifactSummaryResponse(
        id=artifact.id,
        kind=artifact.kind,
        owner=_owner(artifact),
        relative_path=artifact.relative_path,
        display_name=_display_name(artifact),
        revision=artifact.revision,
        processing_status=artifact_processing_status(artifact),
        index_status=artifact_index_status(artifact),
        recovery_required=artifact_recovery_required(artifact),
        allowed_operations=sorted(ALLOWED_OPERATIONS.get(artifact.kind, set())),
        created_at=artifact.created_at,
        updated_at=artifact.updated_at,
    )


def _display_name(artifact) -> str:
    source_filename = artifact_source_filename(artifact)
    if artifact.kind == "source" and source_filename:
        return source_filename
    return Path(artifact.relative_path).name


def _owner(artifact) -> str:
    if artifact.kind == "manifest":
        return "workspace"
    if artifact.kind == "source":
        return "sources"
    if artifact.kind in {"candidate_profile", "target_profile"}:
        return "profile"
    if artifact.kind in {"mastery", "plan"}:
        return "state"
    parts = Path(artifact.relative_path).parts
    return parts[0] if parts else artifact.kind
