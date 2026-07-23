from __future__ import annotations

from typing import Literal
from urllib.parse import quote
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import SessionDep, get_current_user
from app.core.config import Settings, get_settings
from app.db import models
from app.db.session import session_scope
from app.schemas.knowledge import (
    KnowledgeDocumentContentResponse,
    KnowledgeDocumentListResponse,
    KnowledgeDocumentResponse,
)
from app.schemas.resources import ResourceId
from app.services.extraction_service import DOCX_MIME_TYPE
from app.services.knowledge_document_service import (
    InactiveDocumentCleanup,
    KnowledgeCleanupError,
    KnowledgeDocumentService,
)
from app.services.upload_validation_service import (
    UploadPolicy,
    UploadValidationError,
    sanitize_filename,
)
from app.storage.object_store import (
    ObjectConflict,
    ObjectStoreError,
    ObjectTooLarge,
)


router = APIRouter(
    prefix="/api/knowledge-collections/{collection_id}/documents",
    tags=["knowledge"],
)


class KnowledgeDeleteResponse(BaseModel):
    document_id: str
    status: Literal["cleanup_pending"]


def _cleanup_pending(document_id: str) -> JSONResponse:
    payload = KnowledgeDeleteResponse(
        document_id=document_id,
        status="cleanup_pending",
    )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=payload.model_dump(mode="json"),
    )


def _cleanup_is_complete(
    request: Request,
    service: KnowledgeDocumentService,
    *,
    document_id: str,
) -> bool:
    with request.app.state.session_factory() as read_session:
        state = service.repository.get_attempt_state(
            read_session,
            document_id=document_id,
        )
    return bool(
        state is not None
        and not state.is_active
        and state.cleanup_attempt_id is None
        and state.error_code is None
    )


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _service(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(
        request.app.state.object_store,
        retriever_factory=request.app.state.knowledge_retriever_factory,
        coordinator=request.app.state.knowledge_document_coordinator,
        max_parsed_chars=settings.knowledge_max_parsed_chars,
    )


def knowledge_upload_policy(settings: Settings) -> UploadPolicy:
    return UploadPolicy(
        max_bytes=settings.knowledge_document_max_bytes,
        allowed_mime_types=frozenset(
            {
                "text/plain",
                "text/markdown",
                "application/pdf",
                DOCX_MIME_TYPE,
            }
        ),
        allowed_extensions=frozenset({".txt", ".md", ".pdf", ".docx"}),
    )


def content_disposition(filename: str) -> str:
    fallback = sanitize_filename(filename)
    encoded = quote(filename, safe="", encoding="utf-8", errors="strict")
    return (
        f'attachment; filename="{fallback}"; '
        f"filename*=UTF-8''{encoded}"
    )


def _upload_error(error: UploadValidationError) -> HTTPException:
    if error.code == "upload_too_large":
        return _error(413, error.code, "Upload exceeds the configured size limit.")
    if error.code == "upload_type_invalid":
        return _error(415, error.code, "Upload type is not supported.")
    return _error(400, error.code, "Upload metadata or content is invalid.")


def _storage_error(error: ObjectStoreError) -> HTTPException:
    if isinstance(error, ObjectTooLarge):
        return _error(413, "upload_too_large", "Upload exceeds the configured size limit.")
    if isinstance(error, ObjectConflict):
        return _error(409, "knowledge_conflict", "Knowledge storage conflict.")
    return _error(503, "knowledge_unavailable", "Knowledge storage is unavailable.")


@router.get("", response_model=KnowledgeDocumentListResponse)
def list_documents(
    collection_id: ResourceId,
    session: SessionDep,
    include_inactive: bool = False,
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
) -> KnowledgeDocumentListResponse:
    if include_inactive:
        collection = service.collection_service.require_manageable(
            session,
            actor=current_user,
            collection_id=collection_id,
        )
    else:
        collection = service.collection_service.require_visible(
            session,
            user_id=current_user.id,
            collection_id=collection_id,
        )
    return KnowledgeDocumentListResponse(
        documents=[
            KnowledgeDocumentResponse.model_validate(document)
            for document in service.repository.list_for_collection(
                session,
                collection_id=collection.id,
                include_inactive=include_inactive,
            )
        ]
    )


@router.post(
    "",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    collection_id: ResourceId,
    request: Request,
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
    settings: Settings = Depends(get_settings),
) -> KnowledgeDocumentResponse:
    try:
        upload = await request.app.state.upload_validation_service.read_required(
            file,
            policy=knowledge_upload_policy(settings),
        )
    except UploadValidationError as error:
        raise _upload_error(error) from error
    try:
        document = await run_in_threadpool(
            service.upload_committed,
            request.app.state.session_factory,
            actor_id=current_user.id,
            collection_id=collection_id,
            upload=upload,
        )
    except ObjectStoreError as error:
        raise _storage_error(error) from error
    return KnowledgeDocumentResponse.model_validate(document)


@router.get("/{document_id}", response_model=KnowledgeDocumentResponse)
def get_document(
    collection_id: ResourceId,
    document_id: ResourceId,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
) -> KnowledgeDocumentResponse:
    document = service.require_visible(
        session,
        user_id=current_user.id,
        collection_id=collection_id,
        document_id=document_id,
    )
    return KnowledgeDocumentResponse.model_validate(document)


@router.get(
    "/{document_id}/content",
    response_model=KnowledgeDocumentContentResponse,
)
def get_parsed_content(
    collection_id: ResourceId,
    document_id: ResourceId,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
) -> KnowledgeDocumentContentResponse:
    document = service.require_visible(
        session,
        user_id=current_user.id,
        collection_id=collection_id,
        document_id=document_id,
    )
    return KnowledgeDocumentContentResponse(
        document_id=document.id,
        content=service.read_parsed(document),
    )


@router.get("/{document_id}/download")
def download_source(
    collection_id: ResourceId,
    document_id: ResourceId,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
) -> Response:
    document = service.require_visible(
        session,
        user_id=current_user.id,
        collection_id=collection_id,
        document_id=document_id,
    )
    stored = service.read_source(document)
    return Response(
        content=stored.data,
        media_type=document.mime_type,
        headers={
            "Content-Disposition": content_disposition(document.name),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{document_id}/reindex",
    response_model=KnowledgeDocumentResponse,
)
def reindex_document(
    collection_id: ResourceId,
    document_id: ResourceId,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
    settings: Settings = Depends(get_settings),
) -> KnowledgeDocumentResponse:
    document = service.require_in_collection(document_id, collection_id, session)
    updated = service.reindex(
        session,
        actor=current_user,
        document_id=document.id,
        processing_timeout_seconds=settings.knowledge_worker_processing_timeout_seconds,
    )
    return KnowledgeDocumentResponse.model_validate(updated)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    responses={
        202: {"model": KnowledgeDeleteResponse},
        204: {"description": "Knowledge Document projections were cleaned."},
    },
)
def delete_document(
    collection_id: ResourceId,
    document_id: ResourceId,
    request: Request,
    session: SessionDep,
    current_user: models.User = Depends(get_current_user),
    service: KnowledgeDocumentService = Depends(_service),
) -> Response:
    cleanup_attempt_id = str(uuid4())
    with service.coordinator.hold(document_id):
        nested = service.require_in_collection(document_id, collection_id, session)
        document = service.isolate_for_delete(
            session,
            actor=current_user,
            document_id=nested.id,
            cleanup_attempt_id=cleanup_attempt_id,
        )
        cleanup = InactiveDocumentCleanup(
            id=document.id,
            user_id=document.user_id,
            collection_id=document.collection_id,
            retriever_type=document.retriever_type,  # type: ignore[arg-type]
        )
        # Isolation is the retrieval authority boundary. The transaction is
        # closed before ObjectStore/Retriever I/O while the process lock fences
        # the Worker for this Document.
        session.commit()

        try:
            service.cleanup_inactive(cleanup)
        except KnowledgeCleanupError:
            with session_scope(request.app.state.session_factory) as cleanup_session:
                marked = service.repository.mark_cleanup_failed_if_inactive(
                    cleanup_session,
                    document_id=document.id,
                    cleanup_attempt_id=cleanup_attempt_id,
                    message="Knowledge cleanup failed.",
                )
            if not marked and _cleanup_is_complete(
                request,
                service,
                document_id=document.id,
            ):
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            return _cleanup_pending(document.id)

        with session_scope(request.app.state.session_factory) as cleanup_session:
            cleared = service.repository.clear_cleanup_error_if_inactive(
                cleanup_session,
                document_id=document.id,
                cleanup_attempt_id=cleanup_attempt_id,
            )
        if not cleared and not _cleanup_is_complete(
            request,
            service,
            document_id=document.id,
        ):
            return _cleanup_pending(document.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
