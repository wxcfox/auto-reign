from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_current_user
from app.db import models
from app.schemas.attachments import AttachmentDraftListResponse, AttachmentResponse
from app.services.attachment_service import (
    AttachmentServiceError,
    sanitize_filename,
)
from app.services.extraction_service import ExtractionError
from app.services.upload_validation_service import UploadValidationError
from app.storage.object_store import (
    ObjectConflict,
    ObjectNotFound,
    ObjectStoreError,
    ObjectStoreUnavailable,
    ObjectTooLarge,
)


router = APIRouter(prefix="/api/attachments", tags=["attachments"])


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _upload_validation_error(error: UploadValidationError) -> HTTPException:
    if error.code == "upload_too_large":
        return _error(413, error.code, "Upload exceeds the configured size limit.")
    if error.code == "upload_type_invalid":
        return _error(415, error.code, "Upload type is not supported.")
    return _error(400, error.code, "Upload metadata or content is invalid.")


def _extraction_error(error: ExtractionError) -> HTTPException:
    if error.code == "extraction_too_large":
        return _error(413, error.code, "Complete extraction exceeds the configured limit.")
    if error.code == "extraction_unsupported":
        return _error(415, error.code, "Attachment type is not supported.")
    return _error(400, error.code, "Attachment content cannot be parsed.")


def _attachment_service_error(error: AttachmentServiceError) -> HTTPException:
    mapping = {
        "attachment_not_found": (404, "Attachment was not found."),
        "attachment_not_ready": (409, "Attachment is unavailable or already bound."),
        "attachment_unavailable": (503, "Attachment content is unavailable."),
        "attachment_corrupt": (503, "Attachment content failed integrity validation."),
    }
    mapped = mapping.get(error.code)
    if mapped is None:
        return _error(500, "attachment_error", "Attachment operation failed.")
    status_code, message = mapped
    return _error(status_code, error.code, message)


def _object_store_error(error: ObjectStoreError) -> HTTPException:
    if isinstance(error, ObjectTooLarge):
        return _error(413, "upload_too_large", "Upload exceeds the configured size limit.")
    if isinstance(error, ObjectConflict):
        return _error(409, "attachment_conflict", "Attachment storage conflict.")
    if isinstance(error, (ObjectNotFound, ObjectStoreUnavailable)):
        return _error(503, "attachment_unavailable", "Attachment storage is unavailable.")
    return _error(503, "attachment_unavailable", "Attachment storage is unavailable.")


def _content_disposition(
    disposition: Literal["inline", "attachment"],
    filename: str,
) -> str:
    fallback = sanitize_filename(filename)
    encoded = quote(filename, safe="", encoding="utf-8", errors="strict")
    return (
        f'{disposition}; filename="{fallback}"; '
        f"filename*=UTF-8''{encoded}"
    )


@router.post("", response_model=AttachmentResponse, status_code=status.HTTP_201_CREATED)
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
) -> AttachmentResponse:
    try:
        validated = await request.app.state.upload_validation_service.read_required(
            file,
            policy=request.app.state.attachment_upload_policy,
        )
    except UploadValidationError as error:
        raise _upload_validation_error(error) from error

    try:
        attachment = await run_in_threadpool(
            request.app.state.attachment_service.create_draft_committed,
            user_id=current_user.id,
            filename=validated.filename,
            media_type=validated.mime_type,
            content=validated.content,
        )
    except ExtractionError as error:
        raise _extraction_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_error(error) from error
    return AttachmentResponse.model_validate(attachment)


@router.get("/drafts", response_model=AttachmentDraftListResponse)
async def list_attachment_drafts(
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> AttachmentDraftListResponse:
    items = await run_in_threadpool(
        request.app.state.attachment_service.list_drafts,
        user_id=current_user.id,
    )
    return AttachmentDraftListResponse(items=items)


@router.get("/{attachment_id}/content")
async def read_attachment_content(
    attachment_id: str,
    request: Request,
    disposition: Literal["inline", "attachment"] = "inline",
    current_user: models.User = Depends(get_current_user),
) -> Response:
    try:
        content = await run_in_threadpool(
            request.app.state.attachment_service.read_original,
            user_id=current_user.id,
            attachment_id=attachment_id,
        )
    except AttachmentServiceError as error:
        raise _attachment_service_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_error(error) from error
    return Response(
        content=content.content,
        media_type=content.mime_type,
        headers={
            "Content-Disposition": _content_disposition(
                disposition,
                content.filename,
            ),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_attachment_draft(
    attachment_id: str,
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> Response:
    try:
        await run_in_threadpool(
            request.app.state.attachment_service.delete_draft,
            user_id=current_user.id,
            attachment_id=attachment_id,
        )
    except AttachmentServiceError as error:
        raise _attachment_service_error(error) from error
    except ObjectStoreError as error:
        raise _object_store_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
