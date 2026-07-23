from __future__ import annotations

from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_current_user
from app.db import models
from app.schemas.subtask_contexts import SubtaskContextBrief, SubtaskContextBriefList
from app.services.subtask_context_service import SubtaskContextServiceError
from app.services.upload_validation_service import UploadValidationError, sanitize_filename


router = APIRouter(prefix="/api/subtask-contexts", tags=["subtask-contexts"])


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )


def _upload_error(error: UploadValidationError) -> HTTPException:
    if error.code == "upload_too_large":
        return _error(413, error.code, "Upload exceeds the configured size limit.")
    if error.code == "upload_type_invalid":
        return _error(415, error.code, "Upload type is not supported.")
    return _error(400, error.code, "Upload metadata or content is invalid.")


def _service_error(error: SubtaskContextServiceError) -> HTTPException:
    mapping = {
        "context_not_found": (404, "Context was not found."),
        "context_not_ready": (409, "Context is unavailable or already bound."),
        "context_invalid": (400, "Context metadata is invalid."),
    }
    mapped = mapping.get(error.code)
    if mapped is None:
        return _error(500, "context_error", "Context operation failed.")
    status_code, message = mapped
    return _error(status_code, error.code, message)


def _content_disposition(
    disposition: Literal["inline", "attachment"],
    filename: str,
) -> str:
    fallback = sanitize_filename(filename)
    encoded = quote(filename, safe="", encoding="utf-8", errors="strict")
    return f'{disposition}; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'


@router.post(
    "/attachments",
    response_model=SubtaskContextBrief,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment_context(
    request: Request,
    file: UploadFile = File(...),
    current_user: models.User = Depends(get_current_user),
) -> SubtaskContextBrief:
    try:
        validated = await request.app.state.upload_validation_service.read_required(
            file,
            policy=request.app.state.attachment_upload_policy,
        )
    except UploadValidationError as error:
        raise _upload_error(error) from error

    try:
        return await run_in_threadpool(
            request.app.state.subtask_context_service.create_attachment_draft,
            user_id=current_user.id,
            filename=validated.filename,
            media_type=validated.mime_type,
            content=validated.content,
        )
    except SubtaskContextServiceError as error:
        raise _service_error(error) from error


@router.get("/drafts", response_model=SubtaskContextBriefList)
async def list_draft_contexts(
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> SubtaskContextBriefList:
    items = await run_in_threadpool(
        request.app.state.subtask_context_service.list_drafts,
        user_id=current_user.id,
    )
    return SubtaskContextBriefList(items=items)


@router.get("/{context_id}/content")
async def read_context_content(
    context_id: int,
    request: Request,
    disposition: Literal["inline", "attachment"] = "inline",
    current_user: models.User = Depends(get_current_user),
) -> Response:
    try:
        content = await run_in_threadpool(
            request.app.state.subtask_context_service.get_content,
            user_id=current_user.id,
            context_id=context_id,
        )
    except SubtaskContextServiceError as error:
        raise _service_error(error) from error
    return Response(
        content=content.content,
        media_type=content.mime_type,
        headers={
            "Content-Disposition": _content_disposition(disposition, content.name),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/{context_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_draft_context(
    context_id: int,
    request: Request,
    current_user: models.User = Depends(get_current_user),
) -> Response:
    try:
        await run_in_threadpool(
            request.app.state.subtask_context_service.delete_draft,
            user_id=current_user.id,
            context_id=context_id,
        )
    except SubtaskContextServiceError as error:
        raise _service_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
