from __future__ import annotations

import base64
from collections.abc import Collection, Sequence
import logging
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy.orm import Session, sessionmaker

from app.db import models
from app.db.session import session_scope
from app.repositories.subtask_context_repository import (
    SubtaskContextRepository,
    SubtaskContextRepositoryError,
)
from app.schemas.subtask_contexts import SubtaskContextBrief, SubtaskContextContent
from app.services.extraction_service import ExtractedContent, ExtractionError, ExtractionService


logger = logging.getLogger(__name__)


class SubtaskContextServiceError(ValueError):
    """Stable domain failure suitable for explicit transport mapping."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _TargetExtraction(Protocol):
    def extract(
        self,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> ExtractedContent: ...


class SubtaskContextService:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        extraction: ExtractionService | None = None,
        repository: SubtaskContextRepository | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.extraction = extraction or ExtractionService()
        self.repository = repository or SubtaskContextRepository()

    def create_attachment_draft(
        self,
        *,
        user_id: int,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> SubtaskContextBrief:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        image_base64 = (
            base64.b64encode(content).decode("ascii")
            if media_type.startswith("image/")
            else None
        )
        with session_scope(self.session_factory) as session:
            context = self.repository.create_draft(
                session,
                user_id=user_id,
                context_type="attachment",
                name=filename,
                status="parsing",
                binary_data=content,
                image_base64=image_base64,
                mime_type=media_type,
                file_extension=Path(filename).suffix.lower() or None,
                file_size=len(content),
            )
            context_id = context.id

        try:
            extracted = self._extract(
                filename=filename,
                media_type=media_type,
                content=content,
            )
        except ExtractionError as error:
            with session_scope(self.session_factory) as session:
                failed = self.repository.mark_failed(
                    session,
                    user_id=user_id,
                    context_id=context_id,
                    error_code=error.code,
                )
                return SubtaskContextBrief.model_validate(failed)
        except Exception:
            self._best_effort_mark_unexpected_failure(
                user_id=user_id,
                context_id=context_id,
            )
            raise

        status = "empty" if extracted.kind != "image" and not extracted.text else "ready"
        with session_scope(self.session_factory) as session:
            parsed = self.repository.mark_parsed(
                session,
                user_id=user_id,
                context_id=context_id,
                extracted_text=extracted.text,
                status=status,
            )
            return SubtaskContextBrief.model_validate(parsed)

    def create_selected_documents_draft(
        self,
        *,
        user_id: int,
        knowledge_id: str,
        document_ids: Sequence[str],
        name: str = "Selected documents",
    ) -> SubtaskContextBrief:
        if user_id <= 0:
            raise ValueError("user_id must be positive")
        if isinstance(document_ids, (str, bytes)):
            raise SubtaskContextServiceError("context_invalid")
        ids = list(document_ids)
        if (
            not isinstance(knowledge_id, str)
            or not ids
            or any(not isinstance(item, str) for item in ids)
        ):
            raise SubtaskContextServiceError("context_invalid")
        canonical_knowledge_id = knowledge_id.strip()
        canonical_ids = [item.strip() for item in ids]
        if (
            not canonical_knowledge_id
            or any(not item for item in canonical_ids)
            or len(canonical_ids) != len(set(canonical_ids))
        ):
            raise SubtaskContextServiceError("context_invalid")
        type_data: dict[str, object] = {
            "knowledge_id": canonical_knowledge_id,
            "document_ids": canonical_ids,
        }
        with session_scope(self.session_factory) as session:
            context = self.repository.create_draft(
                session,
                user_id=user_id,
                context_type="selected_documents",
                name=name,
                status="ready",
                type_data=type_data,
            )
            return SubtaskContextBrief.model_validate(context)

    def list_drafts(self, *, user_id: int) -> tuple[SubtaskContextBrief, ...]:
        with session_scope(self.session_factory) as session:
            return tuple(
                SubtaskContextBrief.model_validate(row)
                for row in self.repository.list_drafts(session, user_id=user_id)
            )

    def list_for_subtasks(
        self,
        *,
        user_id: int,
        subtask_ids: Collection[int],
    ) -> tuple[SubtaskContextBrief, ...]:
        with session_scope(self.session_factory) as session:
            return tuple(
                SubtaskContextBrief.model_validate(row)
                for row in self.repository.list_for_subtasks(
                    session,
                    user_id=user_id,
                    subtask_ids=subtask_ids,
                )
            )

    def bind_drafts(
        self,
        session: Session,
        *,
        user_id: int,
        context_ids: Sequence[int],
        subtask_id: int,
    ) -> list[models.SubtaskContext]:
        try:
            return self.repository.bind_drafts(
                session,
                user_id=user_id,
                context_ids=context_ids,
                subtask_id=subtask_id,
            )
        except SubtaskContextRepositoryError as error:
            raise SubtaskContextServiceError(error.code) from error

    def get_content(
        self,
        *,
        user_id: int,
        context_id: int,
    ) -> SubtaskContextContent:
        with session_scope(self.session_factory) as session:
            context = self.repository.get_content(
                session,
                user_id=user_id,
                context_id=context_id,
            )
            if (
                context is None
                or context.context_type != "attachment"
                or context.binary_data is None
            ):
                raise SubtaskContextServiceError("context_not_found")
            return SubtaskContextContent(
                id=context.id,
                name=context.name,
                mime_type=context.mime_type or "application/octet-stream",
                file_size=(
                    context.file_size
                    if context.file_size is not None
                    else len(context.binary_data)
                ),
                content=context.binary_data,
            )

    def delete_draft(self, *, user_id: int, context_id: int) -> None:
        with session_scope(self.session_factory) as session:
            try:
                self.repository.delete_draft(
                    session,
                    user_id=user_id,
                    context_id=context_id,
                )
            except SubtaskContextRepositoryError as error:
                raise SubtaskContextServiceError(error.code) from error

    def _extract(
        self,
        *,
        filename: str,
        media_type: str,
        content: bytes,
    ) -> ExtractedContent:
        target = getattr(self.extraction, "extract", None)
        if callable(target):
            return cast(_TargetExtraction, self.extraction).extract(
                filename=filename,
                media_type=media_type,
                content=content,
            )
        return self.extraction.extract_required(
            filename=filename,
            mime_type=media_type,
            content=content,
        )

    def _best_effort_mark_unexpected_failure(
        self,
        *,
        user_id: int,
        context_id: int,
    ) -> None:
        try:
            with session_scope(self.session_factory) as session:
                self.repository.mark_failed(
                    session,
                    user_id=user_id,
                    context_id=context_id,
                    error_code="extraction_failed",
                )
        except Exception as error:
            logger.warning(
                "subtask_context_failure_persistence_failed",
                extra={
                    "context_id": context_id,
                    "error_code": "context_failure_persistence_failed",
                    "exception_type": type(error).__name__,
                },
                exc_info=False,
            )
