from __future__ import annotations

from collections.abc import Collection, Sequence
from copy import deepcopy
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


class SubtaskContextRepositoryError(ValueError):
    """Stable persistence failure without an HTTP dependency."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SubtaskRuntimeContextProjection:
    id: int
    subtask_id: int
    context_type: str
    name: str
    image_base64: str | None
    extracted_text: str | None
    mime_type: str | None
    type_data: object


class SubtaskContextRepository:
    def create_draft(
        self,
        session: Session,
        *,
        user_id: int,
        context_type: str,
        name: str,
        status: str,
        binary_data: bytes | None = None,
        image_base64: str | None = None,
        extracted_text: str | None = None,
        mime_type: str | None = None,
        file_extension: str | None = None,
        file_size: int | None = None,
        type_data: dict[str, object] | None = None,
    ) -> models.SubtaskContext:
        context = models.SubtaskContext(
            user_id=user_id,
            subtask_id=0,
            context_type=context_type,
            name=name,
            status=status,
            error_message=None,
            binary_data=binary_data,
            image_base64=image_base64,
            extracted_text=extracted_text,
            text_length=len(extracted_text) if extracted_text is not None else 0,
            mime_type=mime_type,
            file_extension=file_extension,
            file_size=file_size,
            type_data=deepcopy(type_data) if type_data is not None else {},
        )
        session.add(context)
        session.flush()
        return context

    def get(
        self,
        session: Session,
        *,
        user_id: int,
        context_id: int,
    ) -> models.SubtaskContext | None:
        return session.scalar(
            select(models.SubtaskContext).where(
                models.SubtaskContext.id == context_id,
                models.SubtaskContext.user_id == user_id,
            )
        )

    def get_content(
        self,
        session: Session,
        *,
        user_id: int,
        context_id: int,
    ) -> models.SubtaskContext | None:
        return self.get(session, user_id=user_id, context_id=context_id)

    def list_drafts(
        self,
        session: Session,
        *,
        user_id: int,
    ) -> list[models.SubtaskContext]:
        return list(
            session.scalars(
                select(models.SubtaskContext)
                .where(
                    models.SubtaskContext.user_id == user_id,
                    models.SubtaskContext.subtask_id == 0,
                )
                .order_by(models.SubtaskContext.created_at, models.SubtaskContext.id)
            )
        )

    def list_for_subtasks(
        self,
        session: Session,
        *,
        user_id: int,
        subtask_ids: Collection[int],
    ) -> list[models.SubtaskContext]:
        if not subtask_ids:
            return []
        return list(
            session.scalars(
                select(models.SubtaskContext)
                .where(
                    models.SubtaskContext.user_id == user_id,
                    models.SubtaskContext.subtask_id.in_(subtask_ids),
                )
                .order_by(models.SubtaskContext.created_at, models.SubtaskContext.id)
            )
        )

    def list_runtime_for_subtasks(
        self,
        session: Session,
        *,
        user_id: int,
        subtask_ids: Collection[int],
    ) -> list[SubtaskRuntimeContextProjection]:
        """Load only ready, model-relevant MySQL columns in canonical order."""
        if not subtask_ids:
            return []
        rows = session.execute(
            select(
                models.SubtaskContext.id,
                models.SubtaskContext.subtask_id,
                models.SubtaskContext.context_type,
                models.SubtaskContext.name,
                models.SubtaskContext.image_base64,
                models.SubtaskContext.extracted_text,
                models.SubtaskContext.mime_type,
                models.SubtaskContext.type_data,
            )
            .where(
                models.SubtaskContext.user_id == user_id,
                models.SubtaskContext.subtask_id.in_(subtask_ids),
                models.SubtaskContext.status == "ready",
            )
            .order_by(
                models.SubtaskContext.created_at,
                models.SubtaskContext.id,
            )
        )
        return [
            SubtaskRuntimeContextProjection(
                id=row.id,
                subtask_id=row.subtask_id,
                context_type=row.context_type,
                name=row.name,
                image_base64=row.image_base64,
                extracted_text=row.extracted_text,
                mime_type=row.mime_type,
                type_data=deepcopy(row.type_data),
            )
            for row in rows
        ]

    def bind_drafts(
        self,
        session: Session,
        *,
        user_id: int,
        context_ids: Sequence[int],
        subtask_id: int,
    ) -> list[models.SubtaskContext]:
        requested = list(context_ids)
        if not requested:
            return []
        if subtask_id <= 0 or len(requested) != len(set(requested)):
            raise SubtaskContextRepositoryError("context_not_ready")

        rows = list(
            session.scalars(
                select(models.SubtaskContext)
                .where(
                    models.SubtaskContext.id.in_(requested),
                    models.SubtaskContext.user_id == user_id,
                )
                .order_by(models.SubtaskContext.id)
                .with_for_update()
            )
        )
        by_id = {row.id: row for row in rows}
        ordered = [by_id[context_id] for context_id in requested if context_id in by_id]
        if len(ordered) != len(requested) or any(
            row.subtask_id != 0 or row.status != "ready" for row in ordered
        ):
            raise SubtaskContextRepositoryError("context_not_ready")

        for row in ordered:
            row.subtask_id = subtask_id
        session.flush()
        return ordered

    def delete_draft(
        self,
        session: Session,
        *,
        user_id: int,
        context_id: int,
    ) -> bool:
        context = session.scalar(
            select(models.SubtaskContext)
            .where(
                models.SubtaskContext.id == context_id,
                models.SubtaskContext.user_id == user_id,
            )
            .with_for_update()
        )
        if context is None:
            raise SubtaskContextRepositoryError("context_not_found")
        if context.subtask_id != 0:
            raise SubtaskContextRepositoryError("context_not_ready")
        session.delete(context)
        session.flush()
        return True

    def mark_parsed(
        self,
        session: Session,
        *,
        user_id: int,
        context_id: int,
        extracted_text: str | None,
        status: str = "ready",
    ) -> models.SubtaskContext:
        if status not in {"ready", "empty"}:
            raise SubtaskContextRepositoryError("context_status_invalid")
        context = self._get_parsing_draft(
            session,
            user_id=user_id,
            context_id=context_id,
        )
        context.extracted_text = extracted_text
        context.text_length = len(extracted_text) if extracted_text is not None else 0
        context.status = status
        context.error_message = None
        session.flush()
        return context

    def mark_failed(
        self,
        session: Session,
        *,
        user_id: int,
        context_id: int,
        error_code: str,
    ) -> models.SubtaskContext:
        context = self._get_parsing_draft(
            session,
            user_id=user_id,
            context_id=context_id,
        )
        context.extracted_text = None
        context.text_length = 0
        context.status = "failed"
        context.error_message = error_code
        session.flush()
        return context

    def _get_parsing_draft(
        self,
        session: Session,
        *,
        user_id: int,
        context_id: int,
    ) -> models.SubtaskContext:
        context = session.scalar(
            select(models.SubtaskContext)
            .where(
                models.SubtaskContext.id == context_id,
                models.SubtaskContext.user_id == user_id,
                models.SubtaskContext.subtask_id == 0,
                models.SubtaskContext.status == "parsing",
            )
            .with_for_update()
        )
        if context is None:
            raise SubtaskContextRepositoryError("context_not_ready")
        return context
